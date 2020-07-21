import logging
from pathlib import Path
from typing import Union, List, Optional, Type

import faiss
import numpy as np
from faiss.swigfaiss import IndexHNSWFlat

from haystack.database.base import Document
from haystack.database.sql import SQLDocumentStore
from haystack.retriever.base import BaseRetriever

logger = logging.getLogger(__name__)


class FAISSDocumentStore(SQLDocumentStore):
    def __init__(
        self,
        sql_url: str = "sqlite:///",
        index_factory: str = "HNSW4",
        index_buffer_size: int = 10_000,
        vector_size: int = 768,
    ):
        self.index: IndexHNSWFlat = self._create_new_index(index_factory=index_factory, vector_size=vector_size)
        self.index_factory = index_factory
        self.vector_size = vector_size
        self.index_buffer_size = index_buffer_size
        super().__init__(url=sql_url)

    def _create_new_index(self, index_factory: str, vector_size: int):
        index = faiss.index_factory(vector_size, index_factory)
        return index

    def write_documents(self, documents: List[dict]):
        for i in range(0, len(documents), self.index_buffer_size):
            docs_to_write_in_sql = []
            embeddings = []
            for doc in documents[i:i+self.index_buffer_size]:
                meta = doc.get("meta", {})
                meta["vector_id"] = i
                docs_to_write_in_sql.append({**meta, **doc})
                if "embedding" in doc.keys():
                    embeddings.append(doc["embedding"])
            if embeddings:
                vectors = np.asarray(embeddings, dtype=np.float32)
                self.index.add(vectors)
            assert not embeddings or (len(docs_to_write_in_sql) == len(embeddings))
            super(FAISSDocumentStore, self).write_documents(docs_to_write_in_sql)
        super().get_document_count()

    def update_embeddings(self, retriever: Type[BaseRetriever]):
        """
        Updates the embeddings in the the document store using the encoding model specified in the retriever.
        This can be useful if want to add or change the embeddings for your documents (e.g. after changing the retriever config).

        :param retriever: Retriever
        :return: None
        """
        index = self._create_new_index(index_factory=self.index_factory, vector_size=self.vector_size)

        doc_count = self.get_document_count()
        for i in range(0, doc_count, self.index_buffer_size):
            docs = self.get_all_documents(offset=i, limit=self.index_buffer_size)

            passages = [d.text for d in docs]
            logger.info(f"Updating embeddings for {len(passages)} docs ...")
            embeddings = retriever.embed_passages(passages)

            assert len(docs) == len(embeddings)
            index.add(np.asarray(embeddings, dtype=np.float32))

        self.index = index
        print(type(self.index))

    def query_by_embedding(
        self, query_emb: List[float], filters: Optional[dict] = None, top_k: int = 10, index: Optional[str] = None
    ) -> List[Document]:
        if not self.index:
            raise Exception("No index exists. Use 'update_embeddings()` to create an index.")
        query_vector = np.asarray([query_emb], dtype=np.float32)

        _, vector_id_matrix = self.index.search(query_vector, top_k)
        vector_ids_for_query = vector_id_matrix[0]

        document_ids = [str(v_id + 1) for v_id in vector_ids_for_query if v_id != -1]
        documents = self.get_documents_by_id(document_ids)
        # TODO add deprecation warning and use only one under the hood.
        return documents

    def save(self, file_path: Union[str, Path]):
        faiss.write_index(self.index, str(file_path))

    def load(self, file_path: Union[str, Path]):
        self.index = faiss.read_index(str(file_path))
