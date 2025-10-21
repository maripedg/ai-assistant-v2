from __future__ import annotations

from backend.core.db.engine import resolve_db_url, mask_url, whoami, get_engine


def main() -> None:
    url, source = resolve_db_url()
    print(f"effective_url={mask_url(url)} (source={source})")
    with get_engine().connect() as conn:
        info = whoami(conn)
    print(f"service_name={info.get('service_name')} current_schema={info.get('current_schema')}")


if __name__ == "__main__":
    main()

