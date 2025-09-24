from typing import Dict, List, Tuple
from core.ports.embeddings import EmbeddingsPort
from core.ports.vector_store import VectorStorePort
from core.ports.chat_model import ChatModelPort

class RetrievalService:
    def __init__(self, vector_store: VectorStorePort, chat_model: ChatModelPort, cfg: dict):
        self.vs = vector_store
        self.llm = chat_model
        self.cfg = cfg

    def _select_chunks(self, doc_score_pairs: List[Tuple[object, float]]) -> List[dict]:
        # Ordenar por score desc.
        pairs = sorted(doc_score_pairs, key=lambda x: x[1], reverse=True)
        # Dedup por doc_id o source
        dedupe_key = self.cfg.get("retrieval", {}).get("dedupe_by", "doc_id")
        seen = set()
        selected = []
        for doc, score in pairs:
            meta = dict(doc.metadata)
            key = meta.get(dedupe_key) or meta.get("source") or meta.get("chunk_id")
            if key in seen: 
                continue
            seen.add(key)
            meta["text"] = doc.page_content
            meta["score"] = float(score)
            selected.append(meta)
        return selected

    def _build_prompt(self, context_text: str, question: str) -> str:
        return (
            "Responde usando exclusivamente el contexto provisto. "
            "Si la información no está en el contexto, responde '1'.\n\n"
            f"[Contexto]\n{context_text}\n\n[Pregunta]\n{question}\n"
        )

    def _build_enrichment_prompt(self, base_answer: str) -> str:
        return (
            "Eres un asistente técnico. Mejora la redacción y estructura de la siguiente respuesta, "
            "agregando pasos claros y buenas prácticas cuando aplique. No inventes datos.\n\n"
            f"[Respuesta]\n{base_answer}"
        )

    def answer(self, question: str) -> Dict:
        top_k = int(self.cfg["retrieval"]["top_k"])
        thr = float(self.cfg["retrieval"]["similarity_threshold"])
        max_ctx = int(self.cfg["retrieval"]["max_context_chars"])

        # 1) Buscar en OracleVS con score
        docs_scores = self.vs.similarity_search_with_score(question, k=top_k)
        if not docs_scores:
            # Sin señal: fallback directo
            answer2 = self.llm.generate(question)
            return {
                "question": question,
                "answer": "1",  # marca de “no hay contexto suficiente”
                "answer2": answer2,
                "answer3": answer2,
                "retrieved_chunks_metadata": []
            }

        # 2) Evaluar señal y seleccionar chunks
        best_score = float(sorted(docs_scores, key=lambda x: x[1], reverse=True)[0][1])
        selected = self._select_chunks(docs_scores)

        # 3) ¿supera umbral?
        if best_score < thr:
            # Fallback LLM
            answer2 = self.llm.generate(question)
            return {
                "question": question,
                "answer": "1",
                "answer2": answer2,
                "answer3": answer2,
                "retrieved_chunks_metadata": selected
            }

        # 4) Construir contexto y hacer RAG
        ctx = "\n\n".join(m.get("text", "") for m in selected)
        ctx = ctx[:max_ctx]
        rag_prompt = self._build_prompt(ctx, question)
        answer = self.llm.generate(rag_prompt)
        if not answer or answer.strip() == "" or answer.strip() == "1":
            # Si el LLM no pudo usar el contexto
            answer2 = self.llm.generate(question)
            return {
                "question": question,
                "answer": "1",
                "answer2": answer2,
                "answer3": answer2,
                "retrieved_chunks_metadata": selected
            }

        # 5) Enriquecer respuesta (opcional, mantiene contrato answer3)
        enrich_prompt = self._build_enrichment_prompt(answer)
        answer3 = self.llm.generate(enrich_prompt)

        return {
            "question": question,
            "answer": answer,
            "answer2": self.llm.generate(question),  # técnico/libre
            "answer3": answer3,
            "retrieved_chunks_metadata": selected
        }
