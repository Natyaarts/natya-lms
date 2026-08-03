import zipfile
import os

def create_zip():
    with zipfile.ZipFile('backend-release-v19.zip', 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk('.'):
            # Skip virtual environments, git, pycache, and old zip files
            if '.venv' in root or '.git' in root or '__pycache__' in root or 'test_zip_extract' in root:
                continue
                
            for file in files:
                if file.endswith('.zip'):
                    continue
                    
                file_path = os.path.join(root, file)
                # Important: Convert Windows backslashes to forward slashes for Linux!
                arcname = os.path.relpath(file_path, '.').replace('\\', '/')
                zipf.write(file_path, arcname)

if __name__ == '__main__':
    create_zip()
    print("Created backend-release-v19.zip successfully!")
