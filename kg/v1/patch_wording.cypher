// 對「已經載入 kg/dumps/ 備份」的 Neo4j 套用 v1 的用詞修正（見 kg/README.md「與原始文件的差異」）。
// 只改 4 個 Suggestion 節點的 content，其餘不動；重複執行結果相同。
// 全新建庫請直接跑 graph.cypher，不需要這支。

MATCH (:Metric {name: "下肢肌力"})-[:has_level]->(:Criteria {score: 4})-[:give_advice]->(s:Suggestion)
SET s.content = "下肢肌力很好，持續每週下肢肌力訓練（坐站、深蹲、弓箭步），並搭配快走或爬樓梯。 \n【建議數值】每週 66-78 下；每次 11-13 下 x 2-3 組 (深蹲等)；加快走";

MATCH (:Metric {name: "下肢肌力"})-[:has_level]->(:Criteria {score: 3})-[:give_advice]->(s:Suggestion)
SET s.content = "下肢肌力良好，建議規律加入坐站、半蹲、抬腿等訓練，逐步增加次數或阻力。 \n【建議數值】每週 60 下；每次 10 下 x 2-3 組 (坐站/抬腿)；漸增至 15 下";

MATCH (:Metric {name: "心肺耐力"})-[:has_level]->(:Criteria {score: 4})-[:give_advice]->(s:Suggestion)
SET s.content = "心肺耐力很好，持續每週中等強度有氧，並可加入間歇快走或騎車。 \n【建議數值】每週 165-195 分鐘 (中強度) 或 83-98 分鐘 (高強度)";

MATCH (:Metric {name: "心肺耐力"})-[:has_level]->(:Criteria {score: 3})-[:give_advice]->(s:Suggestion)
SET s.content = "心肺耐力良好，持續中等強度有氧（快走、游泳、腳踏車）運動。 \n【建議數值】每週 150 分鐘；每次 30 分鐘";

// 驗證：應回傳 4 列，4 分的 content 以「…很好」開頭、3 分以「…良好」開頭
MATCH (m:Metric)-[:has_level]->(c:Criteria)-[:give_advice]->(s:Suggestion)
WHERE m.name IN ["下肢肌力", "心肺耐力"] AND c.score IN [3, 4]
RETURN m.name, c.score, c.rank, left(s.content, 8) ORDER BY m.name, c.score DESC;
