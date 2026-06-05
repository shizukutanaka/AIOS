"""Example: simple semantic search using embeddings.

Run:  python3 examples/sdk/03_search.py

aictl picks an embedding model that fits your hardware (often
nomic-embed-text on a small GPU, or a larger one if you have it).
"""

import aictl

# A tiny "knowledge base"
docs = [
    "Python is a high-level programming language known for readability.",
    "Rust is a systems language emphasizing memory safety without GC.",
    "Go was designed at Google for concurrent server-side software.",
    "JavaScript runs in browsers and via Node.js for backend services.",
    "Kubernetes orchestrates containerized applications across clusters.",
]

# Embed everything once
print("Embedding documents...")
doc_vectors = aictl.ai.embed(docs)

query = "Which language is best for writing fast safe systems code?"
print(f"\nQuery: {query}\n")

# Embed the query
[query_vector] = aictl.ai.embed(query)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b + 1e-9)


# Rank by similarity
ranked = sorted(
    zip(docs, doc_vectors),
    key=lambda pair: -cosine(query_vector, pair[1]),
)

print("Top matches:")
for doc, vec in ranked[:3]:
    score = cosine(query_vector, vec)
    print(f"  [{score:.3f}] {doc}")
