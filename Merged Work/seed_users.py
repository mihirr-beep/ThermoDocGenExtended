"""Backward-compatible alias. The canonical seed is now `seed.py`.

    docker compose exec web python seed.py
"""
from seed import main

if __name__ == "__main__":
    main()
