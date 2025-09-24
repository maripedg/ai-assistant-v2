def not_none(v, name):
    if v is None:
        raise ValueError(f"Missing required setting: {name}")
    return v
