import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def normalize_interests(items: list[str]) -> list[str]:
    cleaned = []
    for item in items:
        value = re.sub(r"\s+", " ", item.strip().lower())
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned[:50]

def build_text(bio: str, looking_for: str, interests: list[str], vibe: str) -> str:
    return " ".join([bio or "", looking_for or "", " ".join(interests), vibe or ""])

def score_matches(target, candidates):
    if not candidates:
        return []
    documents = [build_text(target.get("bio", ""), target.get("looking_for", ""), target.get("interests", []), target.get("vibe", ""))]
    documents += [build_text(c.get("bio", ""), c.get("looking_for", ""), c.get("interests", []), c.get("vibe", "")) for c in candidates]
    try:
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
        matrix = vectorizer.fit_transform(documents)
        semantic = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
    except ValueError:
        semantic = np.zeros(len(candidates))

    target_interests = set(target.get("interests", []))
    results = []
    for candidate, sim in zip(candidates, semantic):
        candidate_interests = set(candidate.get("interests", []))
        shared = sorted(target_interests & candidate_interests)
        union = target_interests | candidate_interests
        interest_score = len(shared) / len(union) if union else 0.0
        vibe_score = 1.0 if target.get("vibe") and target.get("vibe") == candidate.get("vibe") else 0.0
        location_score = 0.0
        if target.get("district") and target.get("district") == candidate.get("district"):
            location_score += 1.0
        elif target.get("country") and target.get("country") == candidate.get("country"):
            location_score += 0.5
        if target.get("state") and target.get("state") == candidate.get("state") and target.get("country") == candidate.get("country"):
            location_score += 0.5
        age_score = 0.0
        if target.get("age") and candidate.get("age"):
            age_score = max(0.0, 1.0 - abs(target["age"] - candidate["age"]) / 20.0)
        # Interest/vibe lead; demographics are preference filters plus smaller ranking signals.
        score = min(1.0, 0.45 * float(sim) + 0.30 * interest_score + 0.12 * vibe_score + 0.08 * location_score + 0.05 * age_score)
        if shared:
            explanation = f"You both like {', '.join(shared[:4])}."
        elif vibe_score:
            explanation = f"You share a {candidate.get('vibe')} vibe."
        elif target.get("district") and target.get("district") == candidate.get("district"):
            explanation = "You appear to be in the same district."
        elif location_score >= 1:
            explanation = "You share the same country and state."
        elif float(sim) >= 0.08:
            explanation = "Your profiles have some meaningful overlap."
        else:
            explanation = "You may have different interests, but there is potential to discover something new together."
        results.append({
            **candidate,
            "score": round(score * 100, 1),
            "shared_interests": shared,
            "explanation": explanation,
        })
    return sorted(results, key=lambda x: x["score"], reverse=True)
