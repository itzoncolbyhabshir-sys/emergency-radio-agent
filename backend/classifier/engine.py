"""Rule-based incident understanding for the emergency radio agent.

The classifier intentionally stays local and deterministic for contest demos:
it extracts the fields an operator needs to decide what to do next, and it
keeps the original severity/action fields consumed by the existing frontend.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import yaml

SEVERITY_RANK = {"minor": 0, "normal": 1, "urgent": 2, "critical": 3}

SEVERITY_LABELS = {
    "critical": "特急",
    "urgent": "紧急",
    "normal": "一般",
    "minor": "轻微",
}

CN_NUMBER_PATTERN = r"\d+|一|二|两|三|四|五|六|七|八|九|十|多|数"


EVENT_PROFILES = [
    {
        "event_type": "火灾爆炸",
        "severity": "critical",
        "keywords": ["火灾", "着火", "起火", "爆炸", "燃烧", "烟雾", "浓烟"],
        "resources": ["消防救援", "医疗急救", "现场警戒", "供电燃气联动"],
        "actions": [
            "立即派遣消防力量",
            "疏散周边人员",
            "通知医疗机构待命",
            "核查危险源和断电断气需求",
        ],
    },
    {
        "event_type": "地质灾害",
        "severity": "critical",
        "keywords": ["滑坡", "塌方", "泥石流", "山体", "坍塌", "地震", "滚石"],
        "resources": ["消防救援", "工程抢险", "医疗急救", "无人机侦察"],
        "actions": [
            "立即派遣救援队伍",
            "封控危险区域",
            "调度工程抢险设备",
            "建立二次灾害观察点",
        ],
    },
    {
        "event_type": "城市内涝",
        "severity": "urgent",
        "keywords": ["积水", "内涝", "洪水", "暴雨", "水位", "淹水", "涵洞"],
        "resources": ["排涝设备", "交通疏导", "消防救援", "应急照明"],
        "actions": [
            "派出排涝和巡查人员",
            "封闭低洼危险路段",
            "持续监测水位",
            "准备转移受困群众",
        ],
    },
    {
        "event_type": "人员遇险",
        "severity": "critical",
        "keywords": ["被困", "受伤", "伤亡", "失联", "昏迷", "遇险", "求救"],
        "resources": ["消防救援", "医疗急救", "公安协同", "定位搜救"],
        "actions": [
            "核实人员数量和位置",
            "派遣搜救与医疗力量",
            "建立现场联络人",
            "优先保障生命通道",
        ],
    },
    {
        "event_type": "通信故障",
        "severity": "normal",
        "keywords": [
            "基站",
            "信号",
            "断网",
            "通信中断",
            "设备故障",
            "电台故障",
            "链路",
        ],
        "resources": ["通信抢修", "备用电源", "移动基站", "技术值守"],
        "actions": [
            "派发通信抢修工单",
            "启用备用链路",
            "核查供电和天馈系统",
            "同步影响范围",
        ],
    },
    {
        "event_type": "危化泄漏",
        "severity": "critical",
        "keywords": ["泄漏", "毒气", "化学", "异味", "污染", "危化", "腐蚀"],
        "resources": ["危化处置", "消防洗消", "医疗急救", "环境监测"],
        "actions": [
            "建立上风向警戒区",
            "通知危化处置力量",
            "疏散下风向人员",
            "启动环境监测",
        ],
    },
    {
        "event_type": "交通阻断",
        "severity": "urgent",
        "keywords": ["交通中断", "道路封闭", "堵塞", "桥梁", "隧道", "事故车辆"],
        "resources": ["交警疏导", "清障车辆", "医疗急救", "道路养护"],
        "actions": [
            "设置绕行路线",
            "派遣清障力量",
            "核查是否有伤员",
            "同步道路管制信息",
        ],
    },
    {
        "event_type": "常规巡查",
        "severity": "minor",
        "keywords": ["巡查", "报告", "收到", "确认", "例行", "测试"],
        "resources": ["值班记录"],
        "actions": ["登记备案", "持续监听", "按计划反馈"],
    },
]


class HybridClassifier:
    """Local hybrid classifier: weighted YAML rules plus structured profiles."""

    def __init__(self, rules_path=None):
        rules_path = (
            Path(rules_path) if rules_path else Path(__file__).parent / "keywords.yaml"
        )
        self.rules = self._load_rules(rules_path)
        self._known_locations = [
            "浦东陆家嘴中心广场",
            "浦东张江医院",
            "黄浦外滩商场",
            "闵行虹桥站",
            "杨浦大桥",
            "徐汇龙华路",
        ]
        self._location_patterns = [
            re.compile(
                r"([\u4e00-\u9fa5A-Za-z0-9]{2,14}(?:公路|路|街|大道|隧道|桥|广场|学校|医院|商场|大厦|园区|枢纽|机场|小区|村|镇|站|基站|山|河|水库))"
            ),
            re.compile(
                r"((?:东|南|西|北|中|中心|新区|老城|高新|滨海|山前|河东|河西)[\u4e00-\u9fa5]{0,4}(?:区|片区|站|基站|路段))"
            ),
            re.compile(r"([一二三四五六七八九十\d]+号(?:基站|站点|电台|路口))"),
        ]

    def _load_rules(self, rules_path):
        try:
            with open(rules_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}

    def classify(self, text):
        text = str(text or "").strip()
        if len(text) < 2:
            return self._fallback(text, "文本过短")

        profile_hits = self._match_profiles(text)
        rule_scores, rule_hits = self._score_yaml_rules(text)
        severity = self._choose_severity(profile_hits, rule_scores)
        primary = profile_hits[0]["profile"] if profile_hits else None
        event_type = (
            primary["event_type"] if primary else self._infer_type_from_rules(rule_hits)
        )

        keywords = sorted(
            {kw for hit in profile_hits for kw in hit["keywords"]} | set(rule_hits)
        )
        location = self._extract_location(text)
        casualty = self._extract_casualty(text)
        resources = self._merge_resources(profile_hits)
        actions = self._build_actions(severity, profile_hits, casualty)
        confidence = self._confidence(profile_hits, rule_scores, location, casualty)

        reason_parts = []
        if keywords:
            reason_parts.append("命中 " + "、".join(keywords[:5]))
        if casualty:
            reason_parts.append(f"提到{casualty}")
        if location != "未指明":
            reason_parts.append(f"定位到{location}")

        return {
            "severity": severity,
            "severity_label": SEVERITY_LABELS[severity],
            "event_type": event_type,
            "location": location,
            "keywords": keywords,
            "actions": actions,
            "resources": resources,
            "entities": {
                "location": location,
                "casualty": casualty,
                "risk_objects": self._extract_risk_objects(text),
            },
            "confidence": confidence,
            "review_required": confidence < 0.58,
            "source": "rule_profile" if profile_hits else "fallback",
            "rule_score": rule_scores,
            "summary": self._summary(location, event_type, casualty, severity),
            "reason": (
                "；".join(reason_parts)
                if reason_parts
                else "未命中高价值应急词，建议人工复核"
            ),
        }

    def _match_profiles(self, text):
        hits = []
        for profile in EVENT_PROFILES:
            matched = [kw for kw in profile["keywords"] if kw in text]
            if matched:
                hits.append(
                    {"profile": profile, "keywords": matched, "score": len(matched)}
                )
        if any(hit["profile"]["severity"] in {"critical", "urgent"} for hit in hits):
            hits = [hit for hit in hits if hit["profile"]["event_type"] != "常规巡查"]
        return sorted(
            hits,
            key=lambda item: (
                item["score"],
                SEVERITY_RANK[item["profile"]["severity"]],
            ),
            reverse=True,
        )

    def _score_yaml_rules(self, text):
        scores = {"critical": 0, "urgent": 0, "normal": 0, "minor": 0}
        hits = []
        for severity, keywords in self.rules.get("severity", {}).items():
            if severity not in scores or not isinstance(keywords, dict):
                continue
            for keyword, weight in keywords.items():
                if keyword and keyword in text:
                    scores[severity] += int(weight)
                    hits.append(keyword)
        return scores, hits

    def _choose_severity(self, profile_hits, rule_scores):
        candidates = []
        for hit in profile_hits:
            candidates.append(hit["profile"]["severity"])
        if max(rule_scores.values(), default=0) > 0:
            candidates.append(max(rule_scores, key=rule_scores.get))
        if not candidates:
            return "normal"
        return max(candidates, key=lambda sev: SEVERITY_RANK[sev])

    def _infer_type_from_rules(self, keywords):
        for profile in EVENT_PROFILES:
            if any(kw in profile["keywords"] for kw in keywords):
                return profile["event_type"]
        return "未分类事件"

    def _extract_location(self, text):
        for location in sorted(self._known_locations, key=len, reverse=True):
            if location in text:
                return location
        for pattern in self._location_patterns:
            match = pattern.search(text)
            if match:
                return match.group(1)
        return "未指明"

    def _extract_casualty(self, text):
        patterns = [
            rf"({CN_NUMBER_PATTERN})人(?:员)?(?:被困|受伤|伤亡|失联|遇险)",
            rf"(?:被困|受伤|伤亡|失联|遇险)(?:人员)?({CN_NUMBER_PATTERN})人",
            r"(多人|数人)(?:被困|受伤|失联|遇险)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return ""

    def _extract_risk_objects(self, text):
        objects = []
        for word in [
            "学校",
            "医院",
            "居民区",
            "商场",
            "隧道",
            "桥梁",
            "水库",
            "基站",
            "燃气",
            "危化品",
        ]:
            if word in text:
                objects.append(word)
        return objects

    def _merge_resources(self, profile_hits):
        resources = []
        for hit in profile_hits:
            for resource in hit["profile"]["resources"]:
                if resource not in resources:
                    resources.append(resource)
        return resources or ["值班调度", "人工复核"]

    def _build_actions(self, severity, profile_hits, casualty):
        actions = []
        for hit in profile_hits:
            for action in hit["profile"]["actions"]:
                if action not in actions:
                    actions.append(action)
        if casualty and "优先核实人员数量、位置和生命体征" not in actions:
            actions.insert(0, "优先核实人员数量、位置和生命体征")
        if not actions:
            actions = {
                "critical": [
                    "立即转人工指挥确认",
                    "通知相关救援力量待命",
                    "持续追踪现场补充信息",
                ],
                "urgent": ["派员核查现场", "建立警戒和信息回传", "准备增援力量"],
                "normal": ["派发工单", "记录备案", "等待现场反馈"],
                "minor": ["登记备案", "持续监听", "按计划反馈"],
            }[severity]
        return actions[:5]

    def _confidence(self, profile_hits, rule_scores, location, casualty):
        score = 0.28
        score += min(sum(hit["score"] for hit in profile_hits) * 0.16, 0.42)
        score += min(max(rule_scores.values(), default=0) * 0.035, 0.18)
        if location != "未指明":
            score += 0.08
        if casualty:
            score += 0.08
        return round(min(score, 0.98), 2)

    def _summary(self, location, event_type, casualty, severity):
        location_text = location if location != "未指明" else "未知地点"
        casualty_text = f"，{casualty}" if casualty else ""
        return f"{location_text}发生{event_type}{casualty_text}，等级为{SEVERITY_LABELS[severity]}"

    def summarize_incident(self, incident):
        """Build a rolling incident summary from merged radio messages."""
        summary = incident.get("summary", "")
        history = incident.get("history") or []
        if incident.get("event_count", 0) <= 1 or not history:
            return summary
        latest_text = history[-1].get("text", incident.get("latest_text", ""))
        location = incident.get("location") or "未知地点"
        event_type = incident.get("event_type") or "事件"
        severity = SEVERITY_LABELS.get(incident.get("severity"), incident.get("severity"))
        return (
            f"{location}{event_type}已累计{incident.get('event_count', len(history))}次通联，"
            f"等级为{severity}；最新：{latest_text[:36]}"
        )

    def _fallback(self, text, reason=""):
        location = self._extract_location(text)
        return {
            "severity": "normal",
            "severity_label": SEVERITY_LABELS["normal"],
            "event_type": "未分类事件",
            "location": location,
            "keywords": [],
            "actions": ["转人工复核", "继续监听补充信息", "记录原始通联文本"],
            "resources": ["值班调度", "人工复核"],
            "entities": {"location": location, "casualty": "", "risk_objects": []},
            "confidence": 0.3,
            "review_required": True,
            "source": "fallback",
            "fallback_reason": reason,
            "rule_score": {"critical": 0, "urgent": 0, "normal": 0, "minor": 0},
            "summary": "需要人工复核的未分类通联",
            "reason": reason or "规则未命中",
        }

    def classify_batch(self, texts):
        return [self.classify(text) for text in texts]


class LLMFallbackClassifier(HybridClassifier):
    """Rule fast path plus optional Ollama JSON fallback for long-tail messages."""

    def __init__(
        self,
        rules_path=None,
        api_key=None,
        model=None,
        enabled=None,
        base_url=None,
        timeout=4.0,
        confidence_threshold=0.58,
        summary_enabled=True,
    ):
        super().__init__(rules_path)
        self.api_key = api_key
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        self.enabled = (
            enabled
            if enabled is not None
            else os.environ.get("LLM_FALLBACK_ENABLED", "").lower()
            in {"1", "true", "yes", "on"}
        )
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.timeout = float(timeout)
        self.confidence_threshold = float(confidence_threshold)
        self.summary_enabled = summary_enabled

    def classify(self, text):
        rule_result = super().classify(text)
        if not self._should_use_llm(rule_result):
            return rule_result

        try:
            llm_result = self._classify_with_ollama(text, rule_result)
        except Exception as exc:
            rule_result["llm_error"] = str(exc)
            return rule_result

        merged = self._merge_llm_result(rule_result, llm_result)
        merged["source"] = "llm_fallback"
        merged["review_required"] = merged["confidence"] < self.confidence_threshold
        return merged

    def summarize_incident(self, incident):
        fallback = super().summarize_incident(incident)
        if not self.enabled or not self.summary_enabled or incident.get("event_count", 0) < 2:
            return fallback

        prompt = (
            "你是应急无线电指挥智能体。请综合同一 incident 的多条通联，"
            "只输出 JSON，字段为 summary, actions, resources。summary 不超过 60 个中文字符。\n"
            f"incident={json.dumps(self._incident_for_prompt(incident), ensure_ascii=False)}"
        )
        try:
            data = self._ollama_json(prompt)
        except Exception:
            return fallback
        summary = str(data.get("summary", "")).strip()
        return summary or fallback

    def _should_use_llm(self, rule_result):
        if not self.enabled:
            return False
        return (
            rule_result.get("event_type") == "未分类事件"
            or rule_result.get("confidence", 0) < self.confidence_threshold
            or rule_result.get("review_required") is True
        )

    def _classify_with_ollama(self, text, rule_result):
        prompt = (
            "你是应急无线电通联结构化抽取智能体。"
            "根据原始中文通联文本输出严格 JSON，不要输出解释。"
            "字段：severity(one of critical,urgent,normal,minor), event_type, location, "
            "casualty, risk_objects(array), keywords(array), resources(array), actions(array), "
            "summary, reason, confidence(0-1)。"
            "要求：没有地点写 未指明；不确定时降低 confidence；actions 最多 5 条。\n"
            f"原始文本：{text}\n"
            f"规则初判：{json.dumps(rule_result, ensure_ascii=False)}"
        )
        return self._ollama_json(prompt)

    def _ollama_json(self, prompt):
        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ollama unavailable: {exc}") from exc

        generated = payload.get("response", payload)
        if isinstance(generated, dict):
            return generated
        return self._parse_json_object(str(generated))

    def _parse_json_object(self, text):
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM response did not contain a JSON object")
        return json.loads(text[start : end + 1])

    def _merge_llm_result(self, rule_result, llm_result):
        merged = dict(rule_result)
        severity = llm_result.get("severity")
        if severity not in SEVERITY_LABELS:
            severity = rule_result["severity"]

        location = str(llm_result.get("location") or rule_result["location"]).strip()
        if not location:
            location = "未指明"
        casualty = str(llm_result.get("casualty") or "").strip()
        confidence = self._clamp_confidence(
            llm_result.get("confidence", rule_result.get("confidence", 0.5))
        )

        merged.update(
            {
                "severity": severity,
                "severity_label": SEVERITY_LABELS[severity],
                "event_type": str(llm_result.get("event_type") or rule_result["event_type"]).strip(),
                "location": location,
                "keywords": self._string_list(llm_result.get("keywords")) or rule_result.get("keywords", []),
                "actions": self._string_list(llm_result.get("actions")) or rule_result.get("actions", []),
                "resources": self._string_list(llm_result.get("resources")) or rule_result.get("resources", []),
                "confidence": confidence,
                "summary": str(llm_result.get("summary") or rule_result.get("summary", "")).strip(),
                "reason": str(llm_result.get("reason") or rule_result.get("reason", "")).strip(),
            }
        )
        merged["entities"] = {
            "location": location,
            "casualty": casualty or rule_result.get("entities", {}).get("casualty", ""),
            "risk_objects": self._string_list(llm_result.get("risk_objects"))
            or rule_result.get("entities", {}).get("risk_objects", []),
        }
        return merged

    def _clamp_confidence(self, value):
        try:
            return round(max(0.0, min(1.0, float(value))), 2)
        except (TypeError, ValueError):
            return self.confidence_threshold

    def _string_list(self, value):
        if isinstance(value, str):
            return [value] if value.strip() else []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _incident_for_prompt(self, incident):
        return {
            "incident_id": incident.get("incident_id"),
            "location": incident.get("location"),
            "event_type": incident.get("event_type"),
            "severity": incident.get("severity"),
            "event_count": incident.get("event_count"),
            "history": incident.get("history", [])[-8:],
            "resources": incident.get("resources", []),
            "actions": incident.get("actions", []),
        }


if __name__ == "__main__":
    classifier = HybridClassifier()
    samples = [
        "徐汇龙华路发生道路塌陷，两人被困，需要紧急救援",
        "黄浦外滩商场起火，请立即派消防力量",
        "杨浦大桥附近通信基站信号异常，请技术人员检查",
    ]
    for sample in samples:
        print(sample)
        print(classifier.classify(sample))
