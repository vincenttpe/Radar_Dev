// 摘自原 README：建立 Class → Metric 的橋接關係
MATCH (cl:Class), (m:Metric)
WHERE trim(cl.name) = trim(m.name)
MERGE (cl)-[:HAS_METRIC]->(m);

// 驗證 Sentence → Class → Metric → Criteria → Suggestion 是否接通
MATCH p=(s:Sentence)-[:BELONGS_TO]->(cl:Class)-[:HAS_METRIC]->(m:Metric)-[:has_level]->(cr:Criteria)-[:give_advice]->(ad:Suggestion)
MATCH (s)-[:HAS_SCORE]->(sc:Score)
WHERE toInteger(cr.score) = toInteger(sc.value)
RETURN p
LIMIT 500;
