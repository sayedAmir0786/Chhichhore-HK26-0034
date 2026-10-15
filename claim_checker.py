"""Basic evidence-matching for climate claims.

HOW IT WORKS (simple, explainable):
 1. Split text into keywords (lowercase, remove stop-words, light stemming).
 2. Compare the user's keywords with every claim in data/climate_claims.json
    using cosine similarity of keyword sets (0 = nothing shared, 1 = identical).
 3. If the best score >= THRESHOLD, return that entry's assessment + source.
 4. Otherwise return "Unverified".

This is NOT a trained AI model. To upgrade later, replace check_claim() with a
function that calls an AI API / NLP model and returns the same dictionary.
"""
import json
import math
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLAIMS_FILE = os.path.join(BASE_DIR, "data", "climate_claims.json")
THRESHOLD = 0.5

LABELS = {
    "supported": "Supported by available evidence",
    "contradicted": "Contradicted by available evidence",
    "unverified": "Unverified — additional evidence required",
}
STOPWORDS = set("""a an the is are was were be been to of in on at for and or that this it its by with as from has have
had do does did can will would just so than then very all any about because if i we you they there their our more most
much many also only into which who what when how""".split())
NEGATIONS = set("""not no never isnt arent doesnt dont wasnt hasnt havent cant cannot wont without nothing none neither
nor""".split())


def load_claims():
    with open(CLAIMS_FILE, encoding="utf-8") as f:
        return json.load(f)


def stem(word):
    """Very light stemming so 'forests' matches 'forest' and 'warming' matches 'warm'."""
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text):
    return re.findall(r"[a-z]+", text.lower().replace("'", ""))


def keywords(text):
    return {stem(w) for w in words(text) if w not in STOPWORDS and w not in NEGATIONS}


def has_negation(text):
    return any(w in NEGATIONS for w in words(text))


def similarity(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / math.sqrt(len(a) * len(b))


def check_claim(text, category):
    user_words = keywords(text)
    best, best_score, best_phrase = None, 0.0, ""
    for entry in load_claims():
        for phrase in [entry["claim"]] + entry.get("variants", []):
            score = similarity(user_words, keywords(phrase))
            if entry["category"] == category:
                score += 0.05  # small bonus when the chosen category agrees
            if score > best_score:
                best, best_score, best_phrase = entry, score, phrase
    best_score = round(min(best_score, 1.0), 2)

    if best is None or best_score < THRESHOLD:
        return _unverified("No entry in the evidence collection matched this claim closely enough.", best_score)

    # Opposite wording (e.g. "X is not Y" vs "X is Y") could flip the meaning, so we refuse to guess.
    if has_negation(text) != has_negation(best_phrase):
        return _unverified(
            "A related entry was found (\"%s\"), but its wording differs in negation, so the "
            "matcher cannot safely decide whether your claim agrees with it." % best["claim"], best_score,
            best)
    return {
        "assessment": best["assessment"],
        "assessment_label": LABELS[best["assessment"]],
        "explanation": best["explanation"],
        "matched_claim": best["claim"],
        "source_title": best["source_title"],
        "source_url": best["source_url"],
        "match_score": best_score,
    }


def _unverified(reason, score, entry=None):
    return {
        "assessment": "unverified",
        "assessment_label": LABELS["unverified"],
        "explanation": reason,
        "matched_claim": entry["claim"] if entry else None,
        "source_title": entry["source_title"] if entry else None,
        "source_url": entry["source_url"] if entry else None,
        "match_score": score,
    }
