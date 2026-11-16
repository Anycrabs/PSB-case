"""BM25 по упрощённо нормализованным токенам — запасной лексический канал,
если sparse-веса bge-m3 недоступны (например, эмбеддинги берутся из удалённого API)."""

import math
import re
from collections import Counter

ENDINGS = sorted(
    "ами ями ого его ому ему ыми ими ая яя ое ее ые ие ой ей ий ый ую юю ом ем ах ях ам ям ов ев "
    "ию ия ья ье ьи ть ться ет ют ит ат ят ла ло ли ет ся сь а я о е ы и у ю ь".split(),
    key=len,
    reverse=True,
)
STOP = set("и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне "
           "было вот от меня еще нет о из ему теперь когда даже ну ли если уже или ни быть был него до вас "
           "нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их "
           "чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого "
           "какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда "
           "можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая "
           "много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им "
           "более всегда конечно всю между".split())


def stem(word):
    if word.isdigit() or len(word) <= 4:
        return word
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def tokenize(text):
    words = re.findall(r"[a-zа-яё0-9]+(?:\.[0-9]+)*", text.lower().replace("ё", "е"))
    return [stem(w) for w in words if w not in STOP and (len(w) > 1 or w.isdigit())]


def build_bm25(texts, k1=1.5, b=0.75):
    docs = [Counter(tokenize(t)) for t in texts]
    lengths = [sum(d.values()) for d in docs]
    avg = (sum(lengths) / len(lengths)) if lengths else 1.0
    df = Counter(term for d in docs for term in d)
    n = len(docs)
    idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}
    postings = {}
    for i, d in enumerate(docs):
        norm = k1 * (1 - b + b * lengths[i] / avg)
        for term, tf in d.items():
            postings.setdefault(term, []).append((i, idf[term] * tf * (k1 + 1) / (tf + norm)))
    return {"postings": postings, "n": n}


def search_bm25(bm25, query, allowed=None, k=40):
    scores = Counter()
    for term in set(tokenize(query)):
        for i, weight in bm25["postings"].get(term, []):
            if allowed is None or allowed[i]:
                scores[i] += weight
    return scores.most_common(k)
