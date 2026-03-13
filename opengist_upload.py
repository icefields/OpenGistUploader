#!/usr/bin/env python3
"""
OpenGist Uploader - Upload files to a self-hosted OpenGist instance.

Uses Git push (OpenGist's native method) or REST API with CSRF handling.

Usage:
    python3 opengist_upload.py <file> [options]

Options:
    --visibility <public|unlisted|private>  Default: public
    --description <text>                     Gist description/title
    --name <filename>                        Override filename in gist
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv


def get_csrf_token(session: requests.Session, url: str) -> str:
    """Fetch CSRF token from OpenGist."""
    response = session.get(f"{url.rstrip('/')}/login", timeout=30)
    response.raise_for_status()
    
    # Try various CSRF token locations
    # 1. Meta tag
    match = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', response.text)
    if match:
        return match.group(1)
    
    # 2. Hidden input
    match = re.search(r'<input[^>]*name=["\']_?csrf["\'][^>]*value=["\']([^"\']+)["\']', response.text)
    if match:
        return match.group(1)
    
    # 3. data-csrf attribute
    match = re.search(r'data-csrf=["\']([^"\']+)["\']', response.text)
    if match:
        return match.group(1)
    
    return None


def detect_language(filepath: str) -> str:
    """Detect language from file extension."""
    ext_map = {
        '.md': 'markdown', '.markdown': 'markdown',
        '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
        '.tsx': 'typescript', '.jsx': 'javascript',
        '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml',
        '.toml': 'toml', '.ini': 'ini', '.cfg': 'ini',
        '.sh': 'shell', '.bash': 'shell', '.zsh': 'shell',
        '.rs': 'rust', '.go': 'go', '.java': 'java',
        '.kt': 'kotlin', '.kts': 'kotlin', '.lua': 'lua',
        '.rb': 'ruby', '.php': 'php', '.c': 'c', '.h': 'c',
        '.cpp': 'cpp', '.hpp': 'cpp', '.cs': 'csharp',
        '.swift': 'swift', '.sql': 'sql', '.html': 'html',
        '.htm': 'html', '.css': 'css', '.scss': 'scss',
        '.xml': 'xml', '.svg': 'xml', '.txt': 'text',
    }
    ext = Path(filepath).suffix.lower()
    return ext_map.get(ext, 'text')


def upload_via_git(
    filepath: str,
    url: str,
    username: str,
    password: str,
    description: str = None,
    name: str = None,
) -> str:
    """Upload a file to OpenGist using Git push."""
    
    filepath = Path(filepath)
    filename = name or filepath.name
    content = filepath.read_text(encoding='utf-8')
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Copy file
        (tmpdir / filename).write_text(content, encoding='utf-8')
        
        # Create .opengist metadata
        if description:
            (tmpdir / ".opengist").write_text(f"title: {description}\n", encoding='utf-8')
        
        # Git init and commit
        subprocess.run(['git', 'init'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', f'{username}@opengist.local'],
                      cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', username],
                      cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'add', '.'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', description or filename],
                      cwd=tmpdir, check=True, capture_output=True,
                      env={**os.environ, 'GIT_AUTHOR_NAME': username,
                           'GIT_AUTHOR_EMAIL': f'{username}@opengist.local',
                           'GIT_COMMITTER_NAME': username,
                           'GIT_COMMITTER_EMAIL': f'{username}@opengist.local'})
        
        # Build remote URL with auth
        base_domain = url.replace('https://', '').replace('http://', '')
        remote_url = f"https://{username}:{password}@{base_domain}/init"
        
        subprocess.run(['git', 'remote', 'add', 'origin', remote_url],
                      cwd=tmpdir, check=True, capture_output=True)
        
        result = subprocess.run(['git', 'push', '-u', 'origin', 'master'],
                              cwd=tmpdir, check=False, capture_output=True, text=True)
        
        output = result.stdout + result.stderr
        
        # Parse gist URL from output
        for line in output.split('\n'):
            if 'created here:' in line.lower() or 'new repository' in line.lower():
                parts = line.split('https://')
                if len(parts) > 1:
                    gist_url = 'https://' + parts[1].split()[0].rstrip('/').rstrip('.,;')
                    return gist_url
        
        if result.returncode != 0:
            error_msg = result.stderr.replace(password, '***')
            raise Exception(f"Git push failed: {error_msg}")
        
        # Fallback
        remote_result = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                                       cwd=tmpdir, check=True, capture_output=True, text=True)
        return remote_result.stdout.strip().replace('/init', '').replace(f'{username}:{password}@', f'{username}@')


def upload_via_api(
    filepath: str,
    url: str,
    username: str,
    password: str,
    visibility: str = 'public',
    description: str = None,
    name: str = None,
) -> str:
    """Upload a file to OpenGist using REST API."""
    
    filepath = Path(filepath)
    filename = name or filepath.name
    content = filepath.read_text(encoding='utf-8')
    
    session = requests.Session()
    
    # Get CSRF token
    csrf_token = get_csrf_token(session, url)
    
    # Build payload
    payload = {
        'files': {filename: {'content': content}},
        'public': visibility == 'public',
        'description': description or f"Uploaded from {filepath.name}",
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    
    if csrf_token:
        headers['X-CSRF-Token'] = csrf_token
    
    # Try different auth methods
    # Method 1: Token in Authorization header
    headers['Authorization'] = f'token {password}'
    
    api_url = f"{url.rstrip('/')}/api/v1/gists"
    response = session.post(api_url, json=payload, headers=headers, timeout=30)
    
    # If failed, try Basic auth
    if response.status_code in (400, 401, 403):
        from requests.auth import HTTPBasicAuth
        response = session.post(api_url, json=payload, 
                               auth=HTTPBasicAuth(username, password),
                               headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                               timeout=30)
    
    if response.status_code not in (200, 201):
        raise Exception(f"API upload failed: {response.status_code} - {response.text}")
    
    data = response.json()
    return data.get('html_url') or data.get('url')


def main():
    parser = argparse.ArgumentParser(
        description='Upload files to OpenGist',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('file', help='File to upload')
    parser.add_argument('--visibility', choices=['public', 'unlisted', 'private'],
                        default='public', help='Gist visibility (default: public)')
    parser.add_argument('--description', help='Gist description/title')
    parser.add_argument('--name', help='Override filename in gist')
    parser.add_argument('--username', help='OpenGist username')
    parser.add_argument('--method', choices=['git', 'api'], default='git',
                        help='Upload method (default: git)')
    
    args = parser.parse_args()
    
    # Load environment
    script_dir = Path(__file__).parent
    env_file = script_dir / '.env'
    
    if not env_file.exists():
        print(f"Error: .env file not found at {env_file}", file=sys.stderr)
        sys.exit(1)
    
    load_dotenv(env_file)
    
    url = os.getenv('OPENGIST_URL')
    password = os.getenv('OPENGIST_PASSWORD') or os.getenv('OPENGIST_TOKEN')
    username = args.username or os.getenv('OPENGIST_USERNAME')
    
    if not url or not password:
        print("Error: OPENGIST_URL and OPENGIST_PASSWORD (or OPENGIST_TOKEN) must be set", file=sys.stderr)
        sys.exit(1)
    
    if not username:
        print("Error: OPENGIST_USERNAME must be set or pass --username", file=sys.stderr)
        sys.exit(1)
    
    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    
    try:
        if args.method == 'git':
            gist_url = upload_via_git(
                filepath=str(filepath),
                url=url,
                username=username,
                password=password,
                description=args.description,
                name=args.name,
            )
        else:
            gist_url = upload_via_api(
                filepath=str(filepath),
                url=url,
                username=username,
                password=password,
                visibility=args.visibility,
                description=args.description,
                name=args.name,
            )
        
        print(f"✓ Uploaded: {gist_url}")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()