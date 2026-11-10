from dataclasses import dataclass, field, asdict
import json


class GameError(Exception):
    """可安全向玩家显示的错误。"""


class RenderContentError(GameError):
    """单题格式不能完整渲染；可以暂时隔离该题并尝试下一题。"""


@dataclass
class Sample:
    input: str
    output: str
    explanation: str = ''


@dataclass
class Problem:
    problem_id: str
    platform: str
    title: str
    url: str
    contest_id: str
    original_rating: float
    normalized_rating: int
    statement: str
    input_spec: str
    output_spec: str
    samples: list[Sample]
    official_editorial: str
    editorial_url: str
    constraints: str = ''
    time_limit: str = ''
    memory_limit: str = ''
    core_difficulties: str = ''
    complexity: str = ''
    updated_at: float = 0
    verified: bool = False
    verification: dict = field(default_factory=dict)

    def dump(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def load(cls, value):
        d = json.loads(value) if isinstance(value, str) else dict(value)
        d['samples'] = [Sample(**s) if isinstance(s, dict) else s for s in d['samples']]
        return cls(**d)

    def public(self):
        d = asdict(self)
        for k in ('official_editorial', 'editorial_url', 'verification', 'verified', 'core_difficulties', 'complexity'):
            d.pop(k, None)
        from .statement_quality import checked_public
        return checked_public(d)


@dataclass(frozen=True)
class Identity:
    platform_id: str
    user_id: str
    group_id: str = ''
    display_name: str = ''

    @property
    def room_type(self):
        return 'group' if self.group_id else 'private'

    @property
    def room_id(self):
        return json.dumps([self.platform_id, self.room_type, self.group_id or self.user_id], ensure_ascii=False, separators=(',', ':'))


@dataclass
class Judgment:
    verdict: str = 'UNCERTAIN'
    confidence: float = 0
    main_idea_correct: bool = False
    core_difficulty_covered: bool = False
    complexity_valid: bool = False
    missing_key_points: list[str] = field(default_factory=list)
    logical_errors: list[str] = field(default_factory=list)
    counterexample: str = ''
    feedback: str = '暂时无法确定，请补充关键步骤、适用条件和复杂度。'


@dataclass
class Reply:
    text: str = ''
    images: list[str] = field(default_factory=list)
