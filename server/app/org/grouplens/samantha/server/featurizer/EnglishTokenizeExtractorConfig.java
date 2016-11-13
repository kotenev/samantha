package org.grouplens.samantha.server.featurizer;

import org.grouplens.samantha.modeler.featurizer.FeatureExtractor;
import org.grouplens.samantha.modeler.featurizer.EnglishTokenizeExtractor;
import org.grouplens.samantha.server.io.RequestContext;
import play.Configuration;
import play.inject.Injector;

public class EnglishTokenizeExtractorConfig implements FeatureExtractorConfig {
    private final String indexName;
    private final String feaName;
    private final String attrName;
    private final String vocabularyName;

    private EnglishTokenizeExtractorConfig(String indexName,
                                           String attrName,
                                           String feaName,
                                           String vocabularyName) {
        this.indexName = indexName;
        this.attrName = attrName;
        this.feaName = feaName;
        this.vocabularyName = vocabularyName;
    }

    public FeatureExtractor getFeatureExtractor(RequestContext requestContext) {
        return new EnglishTokenizeExtractor(indexName, attrName, feaName, vocabularyName);
    }

    public static FeatureExtractorConfig
            getFeatureExtractorConfig(Configuration extractorConfig,
                                      Injector injector) {
        return new EnglishTokenizeExtractorConfig(
                extractorConfig.getString("indexName"),
                extractorConfig.getString("attrName"),
                extractorConfig.getString("feaName"),
                extractorConfig.getString("vocabularyName")
        );
    }
}