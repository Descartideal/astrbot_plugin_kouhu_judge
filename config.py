from dataclasses import dataclass, field, fields


@dataclass
class Config:
    enabled_groups: list[str] = field(default_factory=list)
    private_enabled: bool = True
    bare_commands: bool = True
    direct_code: bool = True
    private_generic_commands: bool = False
    judge_provider: str = ''
    translation_provider: str = ''
    translation_attempts: int = 3
    translation_use_dedicated: bool = True
    translation_max_output_tokens: int = 4096
    chat_provider: str = ''
    translate: bool = True
    judge_only: bool = True
    judge_two_stage: bool = False
    api_base_url: str = 'https://api.openai.com/v1'
    api_key: str = field(default='',repr=False)
    api_model: str = 'gpt-6.1-sol'
    api_proxy: str = ''
    api_max_output_tokens: int = 2048
    api_reasoning_effort: str = 'low'
    reuse_identical_submissions: bool = True
    default_platform: str = 'cf'
    default_low: int = 1400
    default_high: int = 1800
    difficulty_mode: str = 'fixed'
    group_defaults: list[str] = field(default_factory=list)
    background_at_download: bool = True
    at_download_batch_size: int = 3
    background_download: bool = True
    group_prefetch: bool = True
    prefetch_interval: float = 60
    download_proxy: str = ''
    idle_seconds: float = 180
    download_interval: float = 60
    download_batch_size: int = 20
    download_low: int = 0
    download_high: int = 3500
    metadata_ttl_hours: float = 24
    download_retry_hours: float = 24
    archive_fallback: bool = True
    compact_layout: bool = True
    timeout: float = 90
    retries: int = 0
    max_submission_chars: int = 100000
    max_history_chars: int = 48000
    max_parallel_judges: int = 3
    ac_confidence: float = 0.85
    wa_feedback_chars: int = 120
    record_practice: bool = True
    dedup: str = 'user_and_room'
    activity_hours: float = 6
    zero_weight: float = 0.2
    solve_weight: float = 1.0
    initiator_bonus: float = 1.25
    difficulty_scale: float = 260
    difficulty_window: int = 400
    recent_penalty: float = 0.25
    at_scale: float = 1.0
    at_offset: float = 400
    rating_version: str = 'approx-v1'
    rating_k: float = 100
    rating_decay: float = 0.25
    rating_floor: int = 5
    first_coefficient: float = 0.75
    first_offset: float = 340
    import_paths: list[str] = field(default_factory=list)
    browser_path: str = ''
    browser_channel: str = ''
    font_path: str = ''
    image_page_height: int = 14000
    translation_version: str = 'zh-compact-v2'
    render_version: str = 'hidden-rating-math-v5'
    avatar_ttl_hours: float = 24

    @classmethod
    def from_mapping(cls, d):
        obj = cls(**{f.name: d[f.name] for f in fields(cls) if f.name in d})
        obj.timeout = max(1, min(float(obj.timeout), 600))
        obj.retries = max(0, min(int(obj.retries), 3))
        obj.translation_attempts=max(3,min(10,int(obj.translation_attempts)))
        obj.translation_max_output_tokens=max(1024,min(16384,int(obj.translation_max_output_tokens)))
        obj.max_parallel_judges = max(1, int(obj.max_parallel_judges))
        obj.wa_feedback_chars=max(60,min(240,int(obj.wa_feedback_chars)))
        if obj.dedup not in ('user_and_room', 'user', 'room', 'none'):
            raise ValueError('不支持的去重策略')
        if obj.at_scale <= 0 or obj.difficulty_scale <= 0 or obj.rating_k <= 0:
            raise ValueError('难度与评分参数必须为正数')
        if not 0<=obj.ac_confidence<=1 or not 0<obj.recent_penalty<=1:
            raise ValueError('置信度须在 0..1，近期题目权重须在 0..1 且不为零')
        if min(obj.activity_hours,obj.zero_weight,obj.initiator_bonus,obj.difficulty_window,obj.rating_floor,obj.max_submission_chars,obj.max_history_chars,obj.avatar_ttl_hours)<=0 or obj.solve_weight<0 or obj.rating_decay<0:
            raise ValueError('抽题权重、限额与低保须为正值，增长/衰减系数不得为负')
        obj.image_page_height=max(1000,min(int(obj.image_page_height),30000))
        if obj.default_platform not in ('cf','at') or obj.difficulty_mode not in ('fixed','adaptive'):
            raise ValueError('默认平台/难度模式无效')
        if not 0<=int(obj.default_low)<=int(obj.default_high)<=5000:
            raise ValueError('默认难度范围须在 0..5000 内')
        if not 0<=int(obj.download_low)<=int(obj.download_high)<=5000:
            raise ValueError('下载难度范围无效')
        obj.prefetch_interval=max(15,min(3600,float(obj.prefetch_interval)))
        obj.at_download_batch_size=max(1,min(10,int(obj.at_download_batch_size)))
        obj.download_batch_size=max(1,min(50,int(obj.download_batch_size)))
        obj.idle_seconds=max(1,float(obj.idle_seconds));obj.download_interval=max(1,float(obj.download_interval))
        if obj.api_reasoning_effort not in ('low','medium','high','xhigh','max'):
            raise ValueError('Sol 推理强度无效')
        from urllib.parse import urlsplit
        obj.api_base_url=obj.api_base_url.strip();obj.api_model=obj.api_model.strip();obj.api_key=obj.api_key.strip();obj.api_proxy=obj.api_proxy.strip()
        endpoint=urlsplit(obj.api_base_url)
        if not endpoint.hostname or not (endpoint.scheme=='https' or endpoint.scheme=='http' and endpoint.hostname in ('127.0.0.1','localhost','::1')):
            raise ValueError('API 地址须用 HTTPS 或本机回环 HTTP')
        obj.api_max_output_tokens=max(256,min(16384,int(obj.api_max_output_tokens)))
        return obj

    def allowed(self, who):
        if not who.group_id:
            return self.private_enabled
        return f'{who.platform_id}:GroupMessage:{who.group_id}' in self.enabled_groups
