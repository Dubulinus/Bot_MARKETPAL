import os

exclude = {'.venv', '__pycache__', '.git', 'marketpal-command-center', '.env'}  # ← sem doplň název

for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in exclude]
    level = root.replace(".", "").count(os.sep)
    indent = "│   " * level + "├── "
    print(f"{indent}{os.path.basename(root)}/")
    for file in files:
        print(f"{'│   ' * (level+1)}├── {file}")