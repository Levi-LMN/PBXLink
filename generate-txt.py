import os
import pkgutil
import sys

def get_imported_modules(directory):
    """Scan Python files in the directory and extract imported modules."""
    imported_modules = set()
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("import ") or line.startswith("from "):
                            parts = line.split()
                            if "import" in parts:
                                imported_modules.add(parts[1].split(".")[0])
    return imported_modules

def filter_third_party_modules(modules):
    """Filter out standard library modules."""
    std_libs = {name for _, name, _ in pkgutil.iter_modules()}
    return {module for module in modules if module not in std_libs}

def write_requirements(modules, output_file="requirements.txt"):
    """Write the modules to a requirements.txt file."""
    with open(output_file, "w", encoding="utf-8") as f:
        for module in sorted(modules):
            f.write(f"{module}\n")

if __name__ == "__main__":
    project_dir = os.getcwd()  # Change this to your project directory if needed
    imported_modules = get_imported_modules(project_dir)
    third_party_modules = filter_third_party_modules(imported_modules)
    write_requirements(third_party_modules)
    print("requirements.txt generated without versions.")