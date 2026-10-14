"""
test_setup.py — pre-flight check script.

Run this before starting the server to verify your environment is
configured correctly and all external dependencies are reachable.

Usage:
    python test_setup.py
"""
import os
import sys


def check(label: str, ok: bool, detail: str = ""):
    status = "✅" if ok else "❌"
    msg = f"{status} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return ok


def main():
    results = []

    # 1. GROQ_API_KEY present
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("GROQ_API_KEY", "")
    results.append(check("GROQ_API_KEY found", bool(key),
                         "" if key else "Add GROQ_API_KEY=gsk_... to your .env file"))

    # 2. Core packages importable
    packages = [
        ("fastapi", "fastapi"),
        ("langgraph", "langgraph"),
        ("openai", "openai"),
        ("duckduckgo_search", "duckduckgo-search"),
        ("bs4", "beautifulsoup4"),
        ("requests", "requests"),
        ("dotenv", "python-dotenv"),
    ]
    for module, pip_name in packages:
        try:
            __import__(module)
            results.append(check(f"Package '{pip_name}' importable", True))
        except ImportError:
            results.append(check(f"Package '{pip_name}' importable", False,
                                 f"Run: pip install {pip_name}"))

    # 3. DuckDuckGo reachable
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text("financial regulation", max_results=1))
        results.append(check("DuckDuckGo reachable", bool(hits),
                             "" if hits else "No results returned — check network"))
    except Exception as e:
        results.append(check("DuckDuckGo reachable", False, str(e)))

    # 4. Groq API reachable (only if key is present)
    if key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
            models = client.models.list()
            results.append(check("Groq API reachable", True,
                                 f"{len(list(models))} models available"))
        except Exception as e:
            results.append(check("Groq API reachable", False, str(e)))
    else:
        results.append(check("Groq API reachable", False, "Skipped — no API key"))

    # 5. Output directory writable
    output_dir = os.environ.get("OUTPUT_DIR", "./outputs")
    try:
        os.makedirs(output_dir, exist_ok=True)
        probe = os.path.join(output_dir, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        results.append(check("Output directory writable", True, output_dir))
    except Exception as e:
        results.append(check("Output directory writable", False, str(e)))

    # Summary
    print()
    passed = sum(results)
    total = len(results)
    if passed == total:
        print("All checks passed — ready to run.")
        print("Start the server with:  uvicorn backend.main:app --reload --port 8000")
    else:
        print(f"{total - passed} check(s) failed — fix the issues above before starting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
