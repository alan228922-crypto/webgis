def _items(payload, *keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _localized(value):
    return value.get("Zh_tw") or value.get("En") if isinstance(value, dict) else value


def _train_type_name(code, value=None):
    if isinstance(value, dict):
        value = value.get("Zh_tw") or value.get("En")
    return value or {"1": "自強號", "2": "莒光號", "3": "區間車", "4": "復興號", "5": "普悠瑪號", "6": "太魯閣號"}.get(str(code), "臺鐵列車")
