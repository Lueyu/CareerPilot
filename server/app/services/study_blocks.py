"""Bounded, declarative teaching artifacts. Source indexes refer to this message only."""
import json
import math
import re

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


def teaching_diagram_fallback(question):
    if TEXT_ONLY.search(question) or not (QUEUE.search(question) and WORKER.search(question) and RECOVERY.search(question)):
        return None
    return normalize({
        'type': 'diagram', 'kind': 'sequence', 'basis': 'teaching',
        'title': '队列与 Worker 的故障恢复示例',
        'summary': '这是通用教学示例。关键窗口在持久化结果之后、确认任务之前；此时崩溃可能导致重新投递，所以结果写入应支持幂等。',
        'nodes': ['任务发起者', '队列', 'Worker', '持久化存储'],
        'edges': [[0, 1, '提交任务'], [1, 2, '投递任务'], [2, 3, '按任务标识幂等写入结果'],
                  [2, 1, '写入成功后确认'], [1, 2, '未确认时可重新投递']],
        'sources': [],
    }, [], 0)


def block_prompt(action, question, sources):
    if TEXT_ONLY.search(question):
        return "用户只要文字。只用文字解释，不输出图解、交互产物、表格或 study-blocks。"
    return '''按需用 Markdown 表格、代码、$公式$。图解、交互请求必须给出相应产物和静态文字解释。即使用户没有要求画图，如果问题涉及多个组件间的交互、先后顺序、状态转换或故障分支，且图能帮助理解，主动给出一张对应的图。正文指出图中最关键的一步；图说明的项目事实必须来自本轮实际读取的代码或测试。缺乏依据时用教学示例或方案，不画成项目现状。使用产物时，回复末尾单独一个 ```study-blocks 的 JSON 代码块，内容是数组，最多 3 项，每项必含 title 和 summary。不要输出 HTML、JS 或 Mermaid。格式示例（替换为当前讨论内容）：
{"type":"diagram","kind":"sequence","basis":"teaching","title":"调用顺序","summary":"描述关键关系","nodes":["用户","队列"],"edges":[[0,1,"提交"]],"sources":[]}。kind 可选 architecture/flow/sequence/state；basis 可选 teaching/project/proposal。project 必须引用本轮实际读取代码的 sources 零起始下标，图中每条关系均应有依据；建议用 proposal。
{"type":"demo","preset":"checkpoint","title":"故障窗口","summary":"改变故障位置观察恢复结果"}。preset 可选 chunk/retry/checkpoint/queue；仅在符合讨论内容时使用。所有交互演示均为教学模拟。
{"type":"chart","title":"等待时长","summary":"示例数据","basis":"teaching","unit":"秒","points":[["A",2],["B",3]]}。教学图必须标示模拟；真实数据 basis 为 observed，每个点还需来源下标和该来源内的数值字段路径，如 ["得分",85,0,["score"]]；无可核实数据不要画真实指标。简短问题不必加块。'''


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
