✔ 需要檔案(其他可以不用)：

pip install -r requirements.txt

知識圖譜兩塊連接：
// 建立 Class → Metric 的橋接關係
MATCH (cl:Class), (m:Metric)
WHERE trim(cl.name) = trim(m.name)
MERGE (cl)-[:HAS_METRIC]->(m);

// 驗證 Sentence → Class → Metric → Criteria → Suggestion 是否接通
MATCH p=(s:Sentence)-[:BELONGS_TO]->(cl:Class)-[:HAS_METRIC]->(m:Metric)-[:has_level]->(cr:Criteria)-[:give_advice]->(ad:Suggestion)
MATCH (s)-[:HAS_SCORE]->(sc:Score)
WHERE toInteger(cr.score) = toInteger(sc.value)
RETURN p
LIMIT 500;

1. neo4j-2026-05-10T15-50-58.dump
   --> 在 docker 的 neo4j 中，載入此 .dump 。 
   
   安裝 Docker:sudo apt install docker.io -y

   啟用：
   sudo systemctl enable docker
   sudo systemctl start docker

   建立容器：
   sudo docker run -d \
     --name neo4j \
     -p 7474:7474 \
     -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/password123 \
     -e NEO4J_ACCEPT_LICENSE_AGREEMENT=yes \
     neo4j:enterprise
   
   停止 Neo4j: sudo docker stop neo4j

   匯入知識圖譜 Dump:
   sudo docker run --rm -i \
     --volumes-from neo4j \
     -e NEO4J_ACCEPT_LICENSE_AGREEMENT=yes \
     neo4j:enterprise \
     neo4j-admin database load neo4j \
     --from-stdin \
     --overwrite-destination=true \
     < neo4j-2026-05-10T15-50-58.dump

   啟動 Neo4j: sudo docker start neo4j

   開啟 Browser: http://localhost:7474
   帳號：neo4j
   密碼：password123
   
   驗證知識圖譜:MATCH p=()-[]->() RETURN p;
   
3. Project1.py
   --> 在 VScode 運行此 code

✔ Project1.py

✔ 切換 Project1.py LLM

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"

可改為 較大版本，ex：

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"

MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"

<img width="1672" height="941" alt="1" src="https://github.com/user-attachments/assets/2ab21b99-9107-4081-9915-b9a4fd0774a7" />
<img width="1672" height="941" alt="2" src="https://github.com/user-attachments/assets/accadad7-6010-42ed-87d0-bfc1df2f6510" />
<img width="1672" height="941" alt="3" src="https://github.com/user-attachments/assets/abafface-e18f-48ad-a948-454e1b92d18c" />


06/26 LLM

啟動環境：source /home/nvidia/Downloads/Health-care-report-LLM-main/.venv/bin/activate
啟動網頁版本llm： python -m uvicorn app:app --host 0.0.0.0 --port 8000

