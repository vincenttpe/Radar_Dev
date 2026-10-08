// 1. 建立中心點
CREATE (root:Category {name: "長者建議"})
// 2. 建立 5 大核心指標 (對應圖中綠色節點)
CREATE (m1:Metric {name: "下肢肌力"}),
       (m2:Metric {name: "單腳站立"}),
       (m3:Metric {name: "來回行走"}),
       (m4:Metric {name: "心肺耐力"}),
       (m5:Metric {name: "步行速度"})
// 連結中心點與指標
CREATE (root)-[:include]->(m1),
       (root)-[:include]->(m2),
       (root)-[:include]->(m3),
       (root)-[:include]->(m4),
       (root)-[:include]->(m5)
// ==========================================
// 3. 下肢肌力 (起站30秒) 結構展開
// ==========================================
CREATE (c1_1:Criteria {name: "起站30秒 18次以上", score: 4, rank: "很好"})
CREATE (s1_1:Suggestion {content: "下肢肌力很好，持續每週下肢肌力訓練（坐站、深蹲、弓箭步），並搭配快走或爬樓梯。 \n【建議數值】每週 66-78 下；每次 11-13 下 x 2-3 組 (深蹲等)；加快走"})
CREATE (m1)-[:has_level]->(c1_1)-[:give_advice]->(s1_1)

CREATE (c1_2:Criteria {name: "起站30秒 14~17次", score: 3, rank: "尚好"})
CREATE (s1_2:Suggestion {content: "下肢肌力良好，建議規律加入坐站、半蹲、抬腿等訓練，逐步增加次數或阻力。 \n【建議數值】每週 60 下；每次 10 下 x 2-3 組 (坐站/抬腿)；漸增至 15 下"})
CREATE (m1)-[:has_level]->(c1_2)-[:give_advice]->(s1_2)

CREATE (c1_3:Criteria {name: "起站30秒 11~13次", score: 2, rank: "普通"})
CREATE (s1_3:Suggestion {content: "下肢肌力普通，建議開始固定做下肢肌力訓練，先從徒手或彈力帶開始。 \n【建議數值】每週 42-54 下；每次 7-9 下 x 2-3 組 (彈力帶/徒手)"})
CREATE (m1)-[:has_level]->(c1_3)-[:give_advice]->(s1_3)

CREATE (c1_4:Criteria {name: "起站30秒 7~10次", score: 1, rank: "稍差"})
CREATE (s1_4:Suggestion {content: "下肢肌力偏弱，建議從椅子坐站、扶椅半蹲等低負荷動作開始，必要時進一步接受復健或專業評估。 \n【建議數值】每週 24-36 下；每次 6-9 下 (需扶椅子)；休 1-2 分鐘"})
CREATE (m1)-[:has_level]->(c1_4)-[:give_advice]->(s1_4)

CREATE (c1_5:Criteria {name: "起站30秒 0~6次", score: 0, rank: "不好"})
CREATE (s1_5:Suggestion {content: "下肢肌力不足，建議先由醫師或物理治療師評估，再安排安全的增肌訓練。 \n【建議數值】每週 18 下以下；每日坐站 2-3 下"})
CREATE (m1)-[:has_level]->(c1_5)-[:give_advice]->(s1_5)
// ==========================================
// 4. 單腳站立 結構展開
// ==========================================
CREATE (c2_1:Criteria {name: "單腳站立 15秒以上", score: 4, rank: "很好"})
CREATE (s2_1:Suggestion {content: "跌倒風險極低，持續每週平衡訓練（單腳站、太極、走直線），維持穩定度。 \n【建議數值】每週 66-78 秒；單腳站 22-26 秒 x 3 次"})
CREATE (m2)-[:has_level]->(c2_1)-[:give_advice]->(s2_1)
CREATE (c2_2:Criteria {name: "單腳站立 9~14秒", score: 3, rank: "尚好"})
CREATE (s2_2:Suggestion {content: "跌倒風險偏低，建議加入單腳站、腳跟腳尖走、側走或後退走，持續提升穩定性。 \n【建議數值】每週 60 秒；單腳站 15-20 秒；腳跟腳尖走 5-10 公尺"})
CREATE (m2)-[:has_level]->(c2_2)-[:give_advice]->(s2_2)

CREATE (c2_3:Criteria {name: "單腳站立 5~8秒", score: 2, rank: "普通"})
CREATE (s2_3:Suggestion {content: "跌倒風險普通，建議同時做平衡+下肢肌力訓練，並增加練習頻率。 \n【建議數值】每週 42-54 秒；單腳站 10-13 秒；搭配側走 5 公尺"})
CREATE (m2)-[:has_level]->(c2_3)-[:give_advice]->(s2_3)

CREATE (c2_4:Criteria {name: "單腳站立 3~4秒", score: 1, rank: "稍差"})
CREATE (s2_4:Suggestion {content: "跌倒風險較高，建議優先做平衡與步態訓練，必要時進一步接受復健或專業評估。 \n【建議數值】每週 24-36 秒；扶椅單腳站 6-9 秒；坐站訓練"})
CREATE (m2)-[:has_level]->(c2_4)-[:give_advice]->(s2_4)

CREATE (c2_5:Criteria {name: "單腳站立 0~2秒", score: 0, rank: "不好"})
CREATE (s2_5:Suggestion {content: "跌倒風險極高，建議先由醫師或物理治療師評估，再規劃安全的平衡訓練。 \n【建議數值】每週 18 秒以下；扶持站立 3-5 秒"})
CREATE (m2)-[:has_level]->(c2_5)-[:give_advice]->(s2_5)
// ==========================================
// 5. 來回行走 (2.44公尺行走) 結構展開
// ==========================================
CREATE (c3_1:Criteria {name: "2.44公尺行走 0~5.4秒", score: 4, rank: "很好"})
CREATE (s3_1:Suggestion {content: "跌倒風險極低，持續規律步行，並搭配肌力與平衡訓練維持步態穩定。 \n【建議數值】每週 165-195 分鐘；每次快走 25-30 分鐘；步速 ≥ 1.0 m/s"})
CREATE (m3)-[:has_level]->(c3_1)-[:give_advice]->(s3_1)

CREATE (c3_2:Criteria {name: "2.44公尺行走 5.5~6.1秒", score: 3, rank: "尚好"})
CREATE (s3_2:Suggestion {content: "跌倒風險偏低，建議持續快走與日常步行，並保留每週的肌力與平衡訓練。 \n【建議數值】每週 150 分鐘；每次快走 20-30 分鐘；步速 0.9-1.0 m/s"})
CREATE (m3)-[:has_level]->(c3_2)-[:give_advice]->(s3_2)

CREATE (c3_3:Criteria {name: "2.44公尺行走 6.2~6.6秒", score: 2, rank: "普通"})
CREATE (s3_3:Suggestion {content: "跌倒風險普通，建議加入快走與下肢肌力訓練，必要時加入步態訓練。 \n【建議數值】每週 105-135 分鐘；每次快走 15-20 分鐘；加步態訓練"})
CREATE (m3)-[:has_level]->(c3_3)-[:give_advice]->(s3_3)

CREATE (c3_4:Criteria {name: "2.44公尺行走 6.7~7.5秒", score: 1, rank: "稍差"})
CREATE (s3_4:Suggestion {content: "跌倒風險較高，建議在專業指導下漸進訓練，必要時進一步接受復健或專業評估。 \n【建議數值】每週 60-90 分鐘；每次慢走 12-18 分鐘；步速 0.6-0.8 m/s"})
CREATE (m3)-[:has_level]->(c3_4)-[:give_advice]->(s3_4)

CREATE (c3_5:Criteria {name: "2.44公尺行走 7.6秒以上", score: 0, rank: "不好"})
CREATE (s3_5:Suggestion {content: "跌倒風險極高，建議先由醫師或物理治療師評估，再規劃安全的行走訓練。 \n【建議數值】每週 45 分鐘以下；每日慢走 3-5 分鐘 x 1 次"})
CREATE (m3)-[:has_level]->(c3_5)-[:give_advice]->(s3_5)

// ==========================================
// 6. 心肺耐力 (抬腳2分鐘) 結構展開
// ==========================================
CREATE (c4_1:Criteria {name: "抬腳2分鐘 112次以上", score: 4, rank: "很好"})
CREATE (s4_1:Suggestion {content: "心肺耐力很好，持續每週中等強度有氧，並可加入間歇快走或騎車。 \n【建議數值】每週 165-195 分鐘 (中強度) 或 83-98 分鐘 (高強度)"})
CREATE (m4)-[:has_level]->(c4_1)-[:give_advice]->(s4_1)

CREATE (c4_2:Criteria {name: "抬腳2分鐘 89~99次", score: 3, rank: "尚好"})
CREATE (s4_2:Suggestion {content: "心肺耐力良好，持續中等強度有氧（快走、游泳、腳踏車）運動。 \n【建議數值】每週 150 分鐘；每次 30 分鐘"})
CREATE (m4)-[:has_level]->(c4_2)-[:give_advice]->(s4_2)
CREATE (c4_3:Criteria {name: "抬腳2分鐘 75~88次", score: 2, rank: "普通"})
CREATE (s4_3:Suggestion {content: "心肺耐力普通，建議採取間歇式訓練，中等強度有氧運動（快走、固定式腳踏車或游泳）。 \n【建議數值】每週 105-135 分鐘；每次 15-25 分鐘；週增 10%"})
CREATE (m4)-[:has_level]->(c4_3)-[:give_advice]->(s4_3)
CREATE (c4_4:Criteria {name: "抬腳2分鐘 50~74次", score: 1, rank: "稍差"})
CREATE (s4_4:Suggestion {content: "心肺耐力偏弱，建議先分段式低強度活動開始，等身體適應後再慢慢加量，必要時進一步接受復健或專業評估。 \n【建議數值】每週 60-90 分鐘；每次 12-18 分鐘"})
CREATE (m4)-[:has_level]->(c4_4)-[:give_advice]->(s4_4)
CREATE (c4_5:Criteria {name: "抬腳2分鐘 0~49次", score: 0, rank: "不好"})
CREATE (s4_5:Suggestion {content: "心肺耐力不足，建議先由醫師或物理治療師評估，再規劃安全的有氧訓練。 \n【建議數值】每週 45 分鐘以下；每日 3-5 分鐘"})
CREATE (m4)-[:has_level]->(c4_5)-[:give_advice]->(s4_5)
// ==========================================
// 7. 步行速度 結構展開
// ==========================================
CREATE (c5_1:Criteria {name: "步行速度 1.0 m/s以上", score: 3, rank: "尚好"})
CREATE (s5_1:Suggestion {content: "步行速度良好，建議持續規律步行，並每週加入平衡與下肢肌力訓練，以維持移動能力。 \n【建議數值】每週 150 分鐘；每次 20-30 分鐘；步速 0.8-1.0 m/s"})
CREATE (m5)-[:has_level]->(c5_1)-[:give_advice]->(s5_1)

CREATE (c5_2:Criteria {name: "步行速度 0.8~1.0 m/s", score: 2, rank: "普通"})
CREATE (s5_2:Suggestion {content: "步行速度普通，建議增加日常步行量，並搭配下肢肌力與平衡訓練，逐步提升行走穩定性與速度。 \n【建議數值】每週 105-135 分鐘；每次 15-25 分鐘；步速 0.7 m/s"})
CREATE (m5)-[:has_level]->(c5_2)-[:give_advice]->(s5_2)

CREATE (c5_3:Criteria {name: "步行速度 0.5~0.8 m/s", score: 1, rank: "稍差"})
CREATE (s5_3:Suggestion {content: "步行速度偏慢，建議加強步態訓練與下肢肌力訓練，必要時進一步接受復健或專業評估。 \n【建議數值】每週 60-90 分鐘；每次 12-18 分鐘；步速 0.6 m/s；加平衡"})
CREATE (m5)-[:has_level]->(c5_3)-[:give_advice]->(s5_3)

CREATE (c5_4:Criteria {name: "步行速度 0~0.5 m/s", score: 0, rank: "不好"})
CREATE (s5_4:Suggestion {content: "步行速度極慢，建議先由醫師或物理治療師評估，再規劃安全的步行速度訓練。 \n【建議數值】每週 45 分鐘以下；每日 5-10 分鐘分段走；步速 < 0.6 m/s"})
CREATE (m5)-[:has_level]->(c5_4)-[:give_advice]->(s5_4)


//查看圖(建議知識圖譜)
MATCH p=()-[]->() RETURN p LIMIT 500;

//創建節點(向量)
LOAD CSV WITH HEADERS FROM 'file:///sorted_data.csv' AS row

MERGE (c:Class {name: row.class})
MERGE (sc:Score {value: toInteger(row.score)})

CREATE (s:Sentence {
text: row.coarsed_sentence
})

MERGE (s)-[:BELONGS_TO]->(c)
MERGE (s)-[:HAS_SCORE]->(sc)
//拆分class(向量)// 1. 找到所有名稱含 '+' 的 Class
MATCH (oldC:Class)
WHERE oldC.name CONTAINS '+'

// 2. 使用 split 函數拆開名稱 (例如 "A+B" 變成 ["A", "B"])
WITH oldC, split(oldC.name, '+') AS names

// 3. 針對拆開後的每個名稱，建立（或找到已存在的）新 Class 節點
UNWIND names AS newName
MERGE (newC:Class {name: trim(newName)})

// 4. 找到原本連到舊 Class 的所有句子
WITH oldC, newC
MATCH (s:Sentence)-[:BELONGS_TO]->(oldC)

// 5. 建立句子與新 Class 之間的連線
MERGE (s)-[:BELONGS_TO]->(newC)

// 6. 最後可以手動或加一行指令刪除舊的 oldC (建議先執行上面確認沒問題再刪除)
DETACH DELETE oldC


// 在 Neo4j 中建立向量索引 (假設維度為 1536，視你的模型而定)
CREATE VECTOR INDEX sentence_vectors IF NOT EXISTS
FOR (s:Sentence) ON (s.embedding)
OPTIONS {indexConfig: {
`vector.dimensions`: 1536,
`vector.similarity_function`: 'cosine'
}}


//查看圖(向量搜尋)
MATCH (s:Sentence)-[r:BELONGS_TO]->(c) 
RETURN s, r, c 
LIMIT 550// 2. 驗證整條路徑是否通暢
MATCH p=(:Sentence)-[:BELONGS_TO]->(:Class)-[:has_level]->(:Criteria)-[:give_advice]->(:Suggestion)
RETURN p LIMIT 500;
//向量建立
MATCH (s:Sentence)
RETURN s.embedding
LIMIT 5


//全部的圖
MATCH p=()-[]->() RETURN p LIMIT 1500;

