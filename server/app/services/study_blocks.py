"""Bounded, declarative teaching artifacts. Source indexes refer to this message only."""
import json
import math
import re

from app.i18n import message

FENCE = re.compile(r"(?:^|\n)```study-blocks[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*$")
KINDS = {'architecture', 'flow', 'sequence', 'state'}
PRESETS = {'chunk', 'retry', 'checkpoint', 'queue'}

QUEUE = re.compile(r'队列|\bqueues?\b', re.I)
WORKER = re.compile(r'消费者|工作进程|\bworkers?\b', re.I)
RELATION = re.compile(r'交互|调用链|数据流|流转|先后|状态转换|故障|失败|恢复|怎么.*(?:配合|串)|interact|sequence|data flow|transition|failure|recover', re.I)
ARCHITECTURE = re.compile(r'模块|组件|环节|服务|架构|modules?|components?|services?|architecture', re.I)
COOPERATION = re.compile(r'协作|配合|交互|连接|串联|调用|关系|work together|interact|connect|relationship', re.I)
RECOVERY = re.compile(r'故障|失败|崩溃|宕机|重试|恢复|重复|重新投递|幂等|failure|crash|retry|recover|duplicate|redeliver|idempoten', re.I)
TEXT_ONLY = re.compile(r'(?:不要|不用|无需|别).{0,8}(?:图解|画图|图表|图示|配图)|(?:只要|只用|仅用|仅需|纯)\s*(?:文字|文本)|no diagrams?|text only|only (?:text|prose)', re.I)


def wants_teaching_diagram(action, question):
    return (action in ('explain', 'mechanism', 'why', 'project', 'improve')
            and not TEXT_ONLY.search(question)
            and (bool(ARCHITECTURE.search(question) and COOPERATION.search(question))
                 or bool(QUEUE.search(question) and WORKER.search(question) and RECOVERY.search(question))
                 or (len(question) >= 35 and bool(RELATION.search(question)))))


def teaching_diagram_fallback(question, language=None):
    if TEXT_ONLY.search(question) or not (QUEUE.search(question) and WORKER.search(question) and RECOVERY.search(question)):
        return None
    return normalize({
        'type': 'diagram', 'kind': 'sequence', 'basis': 'teaching',
        'title': message("diagram.title", language),
        'summary': message("diagram.summary", language),
        'nodes': [message("diagram.sender", language), message("diagram.queue", language), 'Worker', message("diagram.storage", language)],
        'edges': [[0, 1, message("diagram.submit", language)], [1, 2, message("diagram.deliver", language)], [2, 3, message("diagram.write", language)],
                  [2, 1, message("diagram.ack", language)], [1, 2, message("diagram.redeliver", language)]],
        'sources': [],
    }, [], 0)


def block_prompt(action, question, sources):
    return message("blocks.text_only" if TEXT_ONLY.search(question) else "blocks.instructions")


def text(value, limit):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        raise ValueError('text')
    return value.strip()


def index(value, size):
    if type(value) is not int or not 0 <= value < size:
        raise ValueError('index')
    return value


def rows(value, low, high):
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError('list')
    return value


def normalize(raw, sources, position):
    if not isinstance(raw, dict):
        raise ValueError('object')
    kind = raw['type']
    base = dict(id=f'b{position+1}', type=kind, title=text(raw['title'], 100), summary=text(raw['summary'], 1200))
    if kind == 'diagram' and raw['kind'] in KINDS and raw['basis'] in ('teaching', 'project', 'proposal'):
        nodes = [text(n, 72) for n in rows(raw['nodes'], 2, 12)]
        edges = [[index(a, len(nodes)), index(b, len(nodes)), text(label, 100)] for a,b,label in rows(raw['edges'], 1, 20)]
        refs = [index(i, len(sources)) for i in rows(raw.get('sources', []),0,16)]
        if raw['basis'] == 'project' and not any(sources[i].get('kind') in ('project_code','project_test','project_caller','code') and not str(sources[i].get('path') or '').lower().endswith(('.md','.rst')) for i in refs):
            raise ValueError('missing code evidence')
        return dict(base, kind=raw['kind'], basis=raw['basis'], nodes=nodes, edges=edges, sources=refs)
    if kind == 'demo' and raw['preset'] in PRESETS:
        return dict(base, preset=raw['preset'])
    if kind == 'chart' and raw['basis'] in ('observed','teaching'):
        points = []
        for p in rows(raw['points'], 1, 12):
            observed = raw['basis'] == 'observed'
            rows(p, 4 if observed else 2, 4 if observed else 2)
            num = p[1]
            if type(num) not in (float, int) or not math.isfinite(num) or abs(num)>1e12:
                raise ValueError('number')
            point = [text(p[0], 70), num]
            if observed:
                ref = index(p[2], len(sources)); path = rows(p[3],1,6)
                data = json.loads(sources[ref]['text'])
                for key in path:
                    data = data[text(key,100)] if isinstance(data,dict) else data[index(key,len(data))]
                if type(data) not in (int,float) or data != num:
                    raise ValueError('unsupported metric')
                point.extend([ref,path])
            points.append(point)
        return dict(base, basis=raw['basis'], unit=text(raw['unit'],25), points=points)
    raise ValueError('unsupported block')


def extract_blocks(content, sources):
    match = FENCE.search(content)
    if not match or len(match[1]) > 24576:
        return content, []
    try:
        blocks = [normalize(raw, sources or [], i) for i,raw in enumerate(rows(json.loads(match[1]),1,3))]
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError):
        return content, []
    return content[:match.start()].rstrip(), blocks
