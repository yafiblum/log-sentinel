"""
run_agent.py: נקודת הכניסה של agent.exe.

PyInstaller צריך קובץ Python להתחיל ממנו (הוא לא יודע להריץ "python -m agent.cli").
הקובץ הזה רק קורא ל-main() של ה-CLI. כל הלוגיקה נמצאת ב-agent/.

בנייה:
    .venv\\Scripts\\pyinstaller --onefile --name agent --console --collect-data tiktoken_ext --hidden-import tiktoken_ext.openai_public run_agent.py
"""

from agent.cli import main

if __name__ == "__main__":
    main()
