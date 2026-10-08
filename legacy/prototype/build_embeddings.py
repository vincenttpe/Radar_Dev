import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "true"

from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase

# =========================
# 0. Neo4j 連線資訊（你少這段）
# =========================
URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]  # 不再寫死於程式碼；請設定環境變數

# =========================
# 1. model
# =========================
embedder = SentenceTransformer("all-MiniLM-L6-v2")

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

# =========================
# 2. 建 embedding
# =========================
with driver.session() as session:

    result = session.run("""
        MATCH (s:Sentence)
        WHERE s.embedding IS NULL
        RETURN id(s) AS id, s.text AS text
    """)

    for record in result:
        node_id = record["id"]
        text = record["text"]

        if text is None:
            continue

        embedding = embedder.encode(text).tolist()

        session.run("""
            MATCH (s) WHERE id(s) = $id
            SET s.embedding = $embedding
        """,
        id=node_id,
        embedding=embedding
        )

print("✅ embedding 建立完成！")