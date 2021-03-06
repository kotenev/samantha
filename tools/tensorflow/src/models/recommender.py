
import tensorflow as tf

from src.builder import ModelBuilder
from src.models import metrics


class RecommenderBuilder(ModelBuilder):

    def __init__(self,
                 user_model,
                 prediction_model,
                 page_size=3,
                 attr2config=None,
                 embedding_attrs=None,
                 target2config=None,
                 eval_metrics='MAP@1',
                 loss_split_steps=500,
                 max_train_steps=500,
                 train_steps=1,
                 eval_steps=1,
                 split_tstamp=None,
                 tstamp_attr='tstamp',
                 filter_unrecognized=False):
        self._user_model = user_model
        self._prediction_model = prediction_model
        self._page_size = page_size
        if attr2config is None:
            self._attr2config = {
                'action': {
                    'vocab_size': 10,
                    'is_numerical': False,
                    'embedding_dim': 10,
                    'level': 'item',
                }
            }
        else:
            self._attr2config = attr2config
        if embedding_attrs is None:
            self._embedding_attrs = []
            for attr in self._attr2config.keys():
                if attr != tstamp_attr:
                    self._embedding_attrs.append(attr)
        else:
            self._embedding_attrs = embedding_attrs
        if target2config is None:
            self._target2config = {
                'action': {
                    'weight': 1.0,
                }
            }
        else:
            self._target2config = target2config
        self._eval_metrics = eval_metrics
        self._loss_split_steps = loss_split_steps
        self._max_train_steps = max_train_steps
        self._train_steps = train_steps
        self._eval_steps = eval_steps
        self._split_tstamp = split_tstamp
        self._tstamp_attr = tstamp_attr
        self._filter_unrecognized = filter_unrecognized
        self._test_tensors = {}

    def test_tensors(self):
        return self._test_tensors

    def _compute_target_loss(self, user_model, indices, labels, paras, target, config, mode):
        batch_idx = tf.reshape(
                tf.slice(indices,
                         begin=[0, 0],
                         size=[tf.shape(indices)[0], 1]),
                [tf.shape(indices)[0], 1])
        step_idx = tf.slice(indices,
                            begin=[0, 1],
                            size=[tf.shape(indices)[0], 1])
        used_step_idx = tf.reshape(step_idx, [tf.shape(indices)[0], 1])
        used_indices = tf.concat([batch_idx, used_step_idx], 1)
        used_model = tf.gather_nd(user_model, used_indices)
        used_labels = tf.gather_nd(labels, indices)
        if self._loss_split_steps < self._max_train_steps and mode == 'train':
            mask_idx = tf.reshape(step_idx, [-1])
            loss = 0
            updates = []
            with tf.variable_scope('mask'):
                for i in range(0, self._max_train_steps, self._loss_split_steps):
                    loss_mask = tf.logical_and(
                            mask_idx >= i, mask_idx < i + self._loss_split_steps)
                    masked_labels = tf.boolean_mask(used_labels, loss_mask)
                    masked_output = tf.boolean_mask(used_model, loss_mask)
                    masked_indices = tf.boolean_mask(indices, loss_mask)
                    masked_loss, model_updates = self._prediction_model.get_target_loss(
                        masked_output, masked_labels, tf.shape(labels), masked_indices,
                        user_model, paras, target, config, mode)
                    loss += masked_loss
                    updates += model_updates
        else:
            loss, updates = self._prediction_model.get_target_loss(
                used_model, used_labels, tf.shape(labels), indices, user_model,
                paras, target, config, mode)
        return loss, updates

    def _compute_target_metrics(self, user_model, indices, labels, paras, target, config):
        used_model, uniq_batch_idx, ori_batch_idx, step_idx = metrics.get_eval_user_model(
                user_model, indices)
        predictions = self._prediction_model.get_target_prediction(
                used_model, paras, target, config)
        used_labels = tf.gather_nd(labels, indices)
        return metrics.compute_eval_label_metrics(
                self._eval_metrics, predictions, used_labels, tf.shape(labels), indices,
                uniq_batch_idx, ori_batch_idx, step_idx)

    def _get_default_train_eval_indices(self, label, start_limit, split_limit, length_limit):
        non_zeros_indices = tf.cast(tf.where(label > 0), tf.int32)
        batch_idx = tf.reshape(tf.slice(
            non_zeros_indices,
            begin=[0, 0],
            size=[tf.shape(non_zeros_indices)[0], 1]), [-1])
        step_idx = tf.reshape(tf.slice(
            non_zeros_indices,
            begin=[0, 1],
            size=[tf.shape(non_zeros_indices)[0], 1]), [-1])
        step_split_limit = tf.gather(split_limit, batch_idx)
        step_length_limit = tf.gather(length_limit, batch_idx)
        step_start_limit = tf.gather(start_limit, batch_idx)
        train_indices = tf.boolean_mask(
            non_zeros_indices, tf.logical_and(
                step_idx >= step_start_limit, step_idx < step_split_limit))
        eval_indices = tf.boolean_mask(
            non_zeros_indices, tf.logical_and(
                step_idx >= step_split_limit, step_idx < step_length_limit))
        return train_indices, eval_indices

    def _get_constrained_steps(self, indices, length_limit, mode):
        batch_idx = tf.reshape(tf.slice(
            indices,
            begin=[0, 0],
            size=[tf.shape(indices)[0], 1]), [-1])
        step_idx = tf.reshape(tf.slice(
            indices,
            begin=[0, 1],
            size=[tf.shape(indices)[0], 1]), [-1])
        start_limit = None
        end_limit = None
        if mode == 'train':
            split_limit = tf.segment_max(step_idx, batch_idx)
            end_limit = tf.gather(split_limit, batch_idx)
            start_limit = tf.maximum(end_limit - self._train_steps, 0)
        elif mode == 'eval':
            split_limit = tf.segment_min(step_idx, batch_idx)
            start_limit = tf.gather(split_limit, batch_idx)
            end_limit = tf.minimum(start_limit + self._eval_steps, length_limit - 1)
        return tf.boolean_mask(
            indices, tf.logical_and(step_idx >= start_limit, step_idx <= end_limit))

    def _get_train_eval_indices_by_tstamp(self, label, tstamp, length_limit):
        train_indices = tf.cast(tf.where(
            tf.logical_and(label > 0, tstamp < self._split_tstamp)), tf.int32)
        eval_indices = tf.cast(tf.where(
            tf.logical_and(label > 0, tstamp >= self._split_tstamp)), tf.int32)
        if self._train_steps < self._max_train_steps:
            train_indices = self._get_constrained_steps(train_indices, length_limit, 'train')
        if self._eval_steps < self._max_train_steps:
            eval_indices = self._get_constrained_steps(eval_indices, length_limit, 'eval')
        return train_indices, eval_indices

    def _get_loss_metrics(self, sequence_length, user_model, attr2input):
        length_limit = tf.minimum(
            tf.reshape(sequence_length, [-1]),
            self._max_train_steps)
        tf.summary.histogram('batch_sequence_length', length_limit)
        split_limit = tf.maximum(length_limit - self._eval_steps, 0)
        start_limit = tf.maximum(split_limit - self._train_steps, 0)
        train_loss = 0.0
        eval_loss = 0.0
        num_train_labels = 0.0
        num_eval_labels = 0.0
        updates = []
        target2paras = {}
        for target, config in self._target2config.iteritems():
            with tf.variable_scope(target):
                if self._split_tstamp is None:
                    train_indices, eval_indices = self._get_default_train_eval_indices(
                        attr2input[target], start_limit, split_limit, length_limit)
                else:
                    train_indices, eval_indices = self._get_train_eval_indices_by_tstamp(
                        attr2input[target], attr2input[self._tstamp_attr], length_limit)
                num_target_train_labels = tf.shape(train_indices)[0]
                num_target_eval_labels = tf.shape(eval_indices)[0]
                num_train_labels += config['weight'] * tf.cast(num_target_train_labels, tf.float32)
                num_eval_labels += config['weight'] * tf.cast(num_target_eval_labels, tf.float32)
                tf.summary.scalar('num_train_labels', num_target_train_labels)
                tf.summary.scalar('num_eval_labels', num_target_eval_labels)
                with tf.variable_scope('paras'):
                    target2paras[target] = self._prediction_model.get_target_paras(target, config)
                with tf.variable_scope('train'):
                    train_target_loss, model_updates = self._compute_target_loss(
                        user_model, train_indices, attr2input[target], target2paras[target],
                        target, config, 'train')
                    updates += model_updates
                with tf.variable_scope('eval'):
                    eval_target_loss, model_updates = self._compute_target_loss(
                        user_model, eval_indices, attr2input[target], target2paras[target],
                        target, config, 'eval')
                    updates += model_updates
                    updates += self._compute_target_metrics(
                        user_model, eval_indices, attr2input[target],
                        target2paras[target], target, config)
            train_loss += config['weight'] * train_target_loss
            eval_loss += config['weight'] * eval_target_loss
        train_loss = train_loss / tf.maximum(1.0, num_train_labels)
        eval_loss = eval_loss / tf.maximum(1.0, num_eval_labels)
        tf.summary.scalar('train_loss', train_loss)
        tf.summary.scalar('eval_loss', eval_loss)
        return train_loss, updates, target2paras

    def _get_inputs(self):
        attr2input = {}
        max_seq_len = None
        for attr, config in self._attr2config.iteritems():
            size = None
            if config['level'] == 'user':
                size = 1
            inputs = tf.placeholder(
                tf.int32, shape=(None, size), name='%s_idx' % attr)
            if config['level'] == 'item':
                max_seq_len = tf.shape(inputs)[1] / self._page_size
                inputs = tf.reshape(inputs, [tf.shape(inputs)[0], max_seq_len, self._page_size])
            if self._filter_unrecognized:
                inputs = inputs * tf.cast(inputs < config['vocab_size'], tf.int32)
            attr2input[attr] = inputs
        if max_seq_len is None:
            raise Exception('There must be an item level attribute in attr2config.')
        sequence_length = tf.placeholder(
                tf.int32, shape=(None, 1), name='sequence_length_val') / self._page_size
        return max_seq_len, sequence_length, attr2input

    def _get_embedders(self):
        attr2embedder = {}
        for attr in self._embedding_attrs:
            config = self._attr2config[attr]
            attr2embedder[attr] = tf.keras.layers.Embedding(
                    config['vocab_size'], config['embedding_dim'], dtype=tf.float32)
        return attr2embedder

    def _get_embeddings(self, attr2input, attr2embedder):
        attr2embedding = {}
        for attr in self._embedding_attrs:
            config = self._attr2config[attr]
            inputs = attr2input[attr]
            if config['level'] == 'item':
                embedding = attr2embedder[attr](inputs)
                embedding = tf.reshape(embedding,
                    [
                        tf.shape(embedding)[0],
                        tf.shape(embedding)[1],
                        self._page_size * config['embedding_dim']
                    ]
                )
            else:
                embedding = attr2embedder[attr](inputs)
            attr2embedding[attr] = embedding
        return attr2embedding

    def _get_prediction(self, sequence_length, user_model, target2paras):
        seq_idx = sequence_length
        batch_idx = tf.expand_dims(tf.range(tf.shape(sequence_length)[0]), 1)
        output_idx = tf.concat([batch_idx, seq_idx], 1)
        model_output = tf.gather_nd(user_model, output_idx)
        target2preds = {}
        for target, config in self._target2config.iteritems():
            target2preds[target] = self._prediction_model.get_target_prediction(
                model_output, target2paras[target], target, config)
        return target2preds

    def build_model(self):
        max_seq_len, sequence_length, attr2input = self._get_inputs()
        with tf.variable_scope('embeddings'):
            attr2embedder = self._get_embedders()
            attr2embedding = self._get_embeddings(attr2input, attr2embedder)
        with tf.variable_scope('user_model'):
            user_model = self._user_model.get_user_model(
                max_seq_len, sequence_length, attr2embedding, self._attr2config)
            initial = tf.zeros([tf.shape(user_model)[0], 1, tf.shape(user_model)[2]])
            user_model = tf.concat([initial, user_model], axis=1)
        with tf.variable_scope('metrics'):
            loss, updates, target2paras = self._get_loss_metrics(
                sequence_length, user_model, attr2input)
        return loss, updates

