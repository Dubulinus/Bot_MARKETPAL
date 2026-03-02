import os

for root, dirs, files in os.walk("."):
    # Přeskoč skryté složky a __pycache__
    dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
    level = root.replace(".", "").count(os.sep)
    indent = "│   " * level + "├── "
    print(f"{indent}{os.path.basename(root)}/")
    for file in files:
        print(f"{'│   ' * (level+1)}├── {file}")