import oracledb
from typing import List, Tuple, Any
from langchain_community.vectorstores.oraclevs import OracleVS
from langchain_community.vectorstores.utils import DistanceStrategy
from core.ports.vector_store import VectorStorePort

class OracleVSStore(VectorStorePort):
    def __init__(self, dsn: str, user: str, password: str, table: str,
                 embeddings, distance: str = "dot_product"):
        self.conn = oracledb.connect(user=user, password=password, dsn=dsn, mode=oracledb.AUTH_MODE_SYSDBA)
        strategy = DistanceStrategy.DOT_PRODUCT if distance == "dot_product" else DistanceStrategy.COSINE
        self.vs = OracleVS(embedding_function=embeddings, client=self.conn,
                           table_name=table, distance_strategy=strategy)

    def similarity_search_with_score(self, query: str, k: int) -> List[Tuple[Any, float]]:
        return self.vs.similarity_search_with_score(query, k=k)
