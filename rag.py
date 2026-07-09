# -*- coding: utf-8 -*-
"""Camada de RAG (retrieval) do sistema de Operacao Autonoma.
Ingestao multi-formato (md, txt, pdf, docx) da documentacao bruta em `knowledge/`:
runbooks, post-mortems e politicas de negocio. Retriever lexical TF-IDF deterministico
(desempate por doc_id). Em producao trocaria por embeddings + reranking; a interface e a mesma.
"""
import os, re, math

def _read_pdf(path):
    from pypdf import PdfReader
    return "\n".join((pg.extract_text() or "") for pg in PdfReader(path).pages)

def _read_docx(path):
    from docx import Document
    return "\n".join(p.text for p in Document(path).paragraphs)

def load_corpus(corpus_dir="knowledge"):
    """Le todos os documentos do corpus e retorna [{doc_id, text}] (ordenado por doc_id)."""
    docs = []
    for root, _, files in os.walk(corpus_dir):
        for f in sorted(files):
            ext = f.lower().rsplit(".", 1)[-1]
            path = os.path.join(root, f)
            try:
                if ext in ("md", "txt"):
                    text = open(path, encoding="utf-8", errors="ignore").read()
                elif ext == "pdf":
                    text = _read_pdf(path)
                elif ext == "docx":
                    text = _read_docx(path)
                else:
                    continue
            except Exception:
                continue
            doc_id = os.path.relpath(path, corpus_dir).replace(os.sep, "/")
            docs.append({"doc_id": doc_id, "text": text})
    docs.sort(key=lambda d: d["doc_id"])
    return docs

def tokenize(text):
    return re.findall(r"[a-z0-9_]+", text.lower())

def build_index(docs):
    """Indice TF-IDF: vetor por documento + idf global."""
    N = len(docs)
    toks = [tokenize(d["text"]) for d in docs]
    df = {}
    for tk in toks:
        for w in set(tk):
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log((N + 1) / (c + 1)) + 1 for w, c in df.items()}
    vecs = []
    for tk in toks:
        tf = {}
        for w in tk:
            tf[w] = tf.get(w, 0) + 1
        n = len(tk) or 1
        vecs.append({w: (tf[w] / n) * idf[w] for w in tf})
    return {"docs": docs, "vecs": vecs, "idf": idf}

def retrieve(index, query, k=3):
    """Retorna [(doc_id, score)] top-k por similaridade de cosseno (desempate por doc_id)."""
    qtok = tokenize(query)
    if not qtok:
        return []
    qtf = {}
    for w in qtok:
        qtf[w] = qtf.get(w, 0) + 1
    idf = index["idf"]
    qv = {w: (qtf[w] / len(qtok)) * idf.get(w, 0.0) for w in qtf}
    qn = math.sqrt(sum(x * x for x in qv.values())) or 1.0
    scored = []
    for i, v in enumerate(index["vecs"]):
        num = sum(qv.get(w, 0.0) * v.get(w, 0.0) for w in qv)
        dn = (math.sqrt(sum(x * x for x in v.values())) or 1.0) * qn
        scored.append((index["docs"][i]["doc_id"], num / dn))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:k]

def make_retriever(corpus_dir="knowledge"):
    """Atalho: carrega o corpus e devolve uma funcao retrieve(query, k)."""
    idx = build_index(load_corpus(corpus_dir))
    return lambda query, k=3: retrieve(idx, query, k)
