import os, glob

files = glob.glob('app/api/admin/*.py')
for f in files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
        
    new_content = content.replace('require_admin', 'require_manager')
    new_content = new_content.replace('prefix="/admin"', 'prefix="/manager"')
    new_content = new_content.replace('prefix=\'/admin\'', 'prefix=\'/manager\'')
    
    if content != new_content:
        with open(f, 'w', encoding='utf-8') as file:
            file.write(new_content)
