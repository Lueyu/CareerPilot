"""Lexical matching for scoped, already-acquired project materials."""
import re
from dataclasses import dataclass
from unicodedata import normalize

from app.services.project_sources import is_safe_source_path


_IDENTIFIER = re.compile(r"(?<![a-zA-Z0-9_])[a-zA-Z_][a-zA-Z0-9_]*")
_CAPITALS = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])")
_HAN = re.compile(r"[\u3400-\u9fff]+")
_PATH = re.compile(r"(?<![a-zA-Z0-9_./-])[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+")
_FILLER = re.compile(
    r"请问|请帮我|给我|结合|我的|这个|一下|如何|怎么|为什么|什么|能否|能不能|有没有|"
    r"讲讲|讲解|解释|说明|分析|看看|查看|打开|阅读|举例|相关|查找|代码|源码|实现|项目|文件|原理|机制|过程|处理|是怎样|的"
)
_COMMON = set("""
    about agent all also and answer app are based can careerpilot code could current describe
    detail did difference does example explain file flow for from give have how implement implementation
    handle handles handling input into its just list look mechanism method model module more most
    only output overview part
    please project provide repo repository sample should show some source string support system
    tell test tests than that the their these this through use used using value want was were what
    when where which while who why will with work works would your
""".split())
_SHORT = {"api", "ci", "db", "ddl", "gpu", "io", "jwt", "llm", "mcp", "oom", "rag",
          "rpc", "sdk", "sql", "sse", "tcp", "ttl", "ui", "url", "wal"}
_CONCEPTS = {
    "幂等": "idempotency idempotent dedup deduplicate",
    "去重": "dedup deduplicate deduplication",
    "重试": "retry retries backoff",
    "回退": "fallback",
    "降级": "fallback degrade",
    "超时": "timeout deadline",
    "并发": "concurrency concurrent semaphore",
    "缓存": "cache caching",
    "断点": "checkpoint",
    "检查点": "checkpoint",
    "恢复": "recover recovery resume",
    "限流": "ratelimit throttle throttling",
    "预算": "budget",
    "配额": "quota",
    "队列": "queue enqueue dequeue",
    "分块": "chunk chunking",
    "切块": "chunk chunking",
    "检索": "retrieval retrieve retriever",
    "向量": "vector embedding embeddings",
    "召回": "recall retrieve retrieval",
    "重排": "rerank reranker reranking",
    "引用": "citation citations",
    "流式": "stream streaming",
    "鉴权": "auth authenticate authorization authentication",
    "工作区": "workspace",
    "工作空间": "workspace",
    "评分": "judge grading assessment",
    "复测": "retest",
    "工具调用": "tool_call tool_calls toolcall toolcalls",
    "路由": "route router routing",
    "模型选择": "routing selector",
    "选择模型": "routing selector",
    "笔记": "notes notebook",
    "学习教练": "study_chat",
    "聊天": "chat",
    "画像": "skill_state",
}


def _identifier_parts(value):
    return tuple(part.casefold() for part in _CAPITALS.sub("_", value).split("_") if part)


def _words(names):
    return {term for name in names for term in (name.casefold(), *_identifier_parts(name))}


def term_hit(term, value):
    if term.isascii():
        return term.casefold() in _words(_IDENTIFIER.findall(value))
    return term in value


@dataclass(frozen=True)
class Search:
    paths: tuple[str, ...]
    identifiers: frozenset[tuple[str, ...]]
    words: frozenset[str]
    aliases: frozenset[str]
    chinese: re.Pattern
    latin: re.Pattern
    latin_fragments: tuple[str, ...]
    skills: frozenset[str]

    @classmethod
    def from_question(cls, question, skills=()):
        text = normalize("NFKC", question[:4000])
        paths = tuple(dict.fromkeys(m[0].removeprefix("./") for m in _PATH.finditer(text)
                                   if is_safe_source_path(m[0].removeprefix("./"))))[:8]
        dotted = [m[0].rsplit(".", 1)[-1] for m in _PATH.finditer(text)
                  if "/" not in m[0] and m[0].removeprefix("./") not in paths]
        text = _PATH.sub(lambda match: " " if match[0].removeprefix("./") in paths else match[0], text)
        explicit = set(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`|\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", text))
        named = {value for pair in explicit for value in pair if value} | set(dotted)
        identifiers = []
        words = []
        for match in _IDENTIFIER.finditer(text):
            token = match[0]
            word = token.casefold()
            if len(word) < 2 or len(word) > 128 or (word in _COMMON and token not in named):
                continue
            if token in named or "_" in token or _CAPITALS.search(token):
                parts = _identifier_parts(token)
                if parts:
                    identifiers.append(parts)
            elif len(word) >= 3 or word in _SHORT:
                words.append(word)
        # Use adjacent Han trigrams and domain terms. Isolated characters and
        # generic question phrases do not qualify a citation.
        concepts = [term for term in _CONCEPTS if term in text]
        spans = _HAN.findall(_FILLER.sub(" ", text))
        grams = dict.fromkeys(term for span in spans for term in
                              (span[i:i + 3] for i in range(len(span) - 2)))
        chinese = list(dict.fromkeys(concepts + list(grams)))[:48]
        if not chinese and len(spans) == 1 and len(spans[0]) == 2:
            chinese = spans
        identifiers = frozenset(list(dict.fromkeys(identifiers))[:16])
        words = frozenset(list(dict.fromkeys(words))[:24])
        aliases = frozenset(word for term in concepts for word in _CONCEPTS[term].split())
        fragments = tuple(sorted(words | aliases | {max(parts, key=len) for parts in identifiers}))
        return cls(paths, identifiers, words, aliases,
                   re.compile("|".join(map(re.escape, chinese)) or r"(?!)"),
                   re.compile("|".join(map(re.escape, fragments)) or r"(?!)", re.ASCII | re.I), fragments,
                   frozenset(word for value in skills[:64] for word in _words(_IDENTIFIER.findall((value or "")[:128]))
                             if len(word) >= 3 and word not in _COMMON))

    def named_path(self, path):
        return any(path == name or path.endswith("/" + name) for name in self.paths)

    def might_match(self, text):
        if not self.has_terms():
            return True
        folded = text.casefold()
        return any(fragment in folded for fragment in self.latin_fragments) or (
            self.chinese.pattern != r"(?!)" and self.chinese.search(text) is not None
        )

    def score(self, text):
        names = set(_IDENTIFIER.findall(text)) if self.latin.search(text) else set()
        exact = sum(_identifier_parts(name) in self.identifiers for name in names) if self.identifiers else 0
        if self.identifiers and not exact:
            return 0
        tokens = _words(names)
        direct = len(tokens & self.words)
        aliases = len(tokens & self.aliases)
        han = len(set(self.chinese.findall(text)))
        if not (exact or direct or aliases or han):
            return 0
        return 20 * exact + 7 * direct + 4 * aliases + 8 * min(3, han) + min(2, len(tokens & self.skills))

    def path_rank(self, path):
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return (100 * self.named_path(path) + 3 * self.score(stem) + self.score(path))

    def has_terms(self):
        return bool(self.identifiers or self.words or self.aliases or self.chinese.pattern != r"(?!)")

    def __bool__(self):
        return bool(self.paths or self.has_terms())
