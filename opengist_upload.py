#!/usr/bin/env python3
"""
OpenGist Uploader - Upload files to a self-hosted OpenGist instance.

Uses Git push to create gists (OpenGist's native method).

Usage:
    python3 opengist_upload.py <file> [options]

Options:
    --visibility <public|unlisted|private>  Default: public
    --description <text>                     Gist description/title
    --name <filename>                        Override filename in gist
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv


def upload_gist(
    filepath: str,
    url: str,
    username: str,
    password: str,
    visibility: str = 'public',
    description: str = None,
    name: str = None,
) -> str:
    """Upload a file to OpenGist using Git push."""
    
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    filename = name or filepath.name
    content = filepath.read_text(encoding='utf-8')
    
    # Create temp directory for git repo
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Copy file to temp dir
        gist_file = tmpdir / filename
        gist_file.write_text(content, encoding='utf-8')
        
        # Create description file if provided
        if description:
            desc_file = tmpdir / ".opengist"
            desc_file.write_text(f"title: {description}\n", encoding='utf-8')
        
        # Init git repo
        subprocess.run(['git', 'init'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', f'{username}@opengist.local'],
                      cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', username],
                      cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'add', '.'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', description or filename], 
                      cwd=tmpdir, check=True, capture_output=True)
        
        # Build remote URL with auth (use password or token)
        # Format: https://username:password@domain/init
        base_domain = url.replace('https://', '').replace('http://', '')
        remote_url = f"https://{username}:{password}@{base_domain}/init"
        
        # Add remote
        subprocess.run(['git', 'remote', 'add', 'origin', remote_url],
                      cwd=tmpdir, check=True, capture_output=True)
        
        # Push to create gist
        result = subprocess.run(['git', 'push', '-u', 'origin', 'master'],
                              cwd=tmpdir, check=False, capture_output=True, text=True)
        
        # Parse output for gist URL
        output = result.stdout + result.stderr
        
        # Look for "Your new repository has been created here: http://..."
        for line in output.split('\n'):
            if 'created here:' in line.lower() or 'new repository' in line.lower():
                # Extract URL
                parts = line.split('https://')
                if len(parts) > 1:
                    gist_url = 'https://' + parts[1].split()[0].rstrip('/')
                    # Remove trailing punctuation
                    gist_url = gist_url.rstrip('.,;')
                    return gist_url
        
        # If push failed, show error
        if result.returncode != 0:
            # Clean up the URL to hide password/token
            error_msg = result.stderr.replace(password, '***TOKEN***')
            error_msg = error_msg.replace(f"{username}:", f"{username}:***TOKEN***")
            raise Exception(f"Git push failed: {error_msg}")
        
        # Fallback: try to get URL from remote
        remote_result = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                                       cwd=tmpdir, check=True, capture_output=True, text=True)
        return remote_result.stdout.strip().replace('/init', '').replace(f'{username}:{password}@', f'{username}@')


def main():
    parser = argparse.ArgumentParser(
        description='Upload files to OpenGist via Git push',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('file', help='File to upload')
    parser.add_argument('--visibility', choices=['public', 'unlisted', 'private'],
                        default='public', help='Gist visibility (default: public)')
    parser.add_argument('--description', help='Gist description/title')
    parser.add_argument('--name', help='Override filename in gist')
    parser.add_argument('--username', help='OpenGist username (required if not in .env)')
    
    args = parser.parse_args()
    
    # Load environment
    script_dir = Path(__file__).parent
    env_file = script_dir / '.env'
    
    if not env_file.exists():
        print(f"Error: .env file not found at {env_file}", file=sys.stderr)
        print("Copy .env.example to .env and fill in your values.", file=sys.stderr)
        sys.exit(1)
    
    load_dotenv(env_file)
    
    url = os.getenv('OPENGIST_URL')
    password = os.getenv('OPENGIST_PASSWORD') or os.getenv('OPENGIST_TOKEN')
    username = args.username or os.getenv('OPENGIST_USERNAME')
    
    if not url or not password:
        print("Error: OPENGIST_URL and OPENGIST_PASSWORD (or OPENGIST_TOKEN) must be set in .env", file=sys.stderr)
        sys.exit(1)
    
    if not username:
        print("Error: OPENGIST_USERNAME must be set in .env or passed as --username", file=sys.stderr)
        sys.exit(1)
    
    try:
        gist_url = upload_gist(
            filepath=args.file,
            url=url,
            username=username,
            password=password,
            visibility=args.visibility,
            description=args.description,
            name=args.name,
        )
        
        print(f"✓ Uploaded: {gist_url}")
        
        if args.visibility != 'public':
            print(f"  Visibility: {args.visibility}")
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error: Git operation failed", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()