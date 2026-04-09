import argparse
import base64
import json
import sys
import urllib.request
import urllib.error
import os
import re
from datetime import datetime

API_URL = "https://smart.processon.com/v1/api/generate_diagram"


def normalize_title(title):
    if not title:
        return "processon-diagram"
    normalized = title.strip("，。；：、 ,.-_")
    if not normalized:
        return "processon-diagram"
    return normalized[:20]


def slugify_filename(title):
    if not title:
        return "processon-diagram"
    slug = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_-]+", "-", title).strip("-_")
    if not slug:
        slug = "processon-diagram"
    return slug[:40]


def save_image_content(title, content_items, output_dir=None):
    if not output_dir:
        output_dir = os.path.join(os.getcwd(), "outputs", "processon")

    saved_paths = []
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    image_index = 1
    title = normalize_title(title)
    filename_slug = slugify_filename(title)

    for item in content_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "image":
            continue
        if item.get("mimeType") != "image/png":
            continue

        image_data = item.get("data", "")
        if not image_data:
            continue

        if image_index == 1:
            filename = f"{filename_slug}-{timestamp}.png"
        else:
            filename = f"{filename_slug}-{timestamp}-{image_index}.png"
        file_path = os.path.abspath(os.path.join(output_dir, filename))
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(image_data))
        saved_paths.append(file_path)
        image_index += 1

    return {
        "title": title,
        "filename_slug": filename_slug,
        "saved_paths": saved_paths,
    }

def generate_diagram(prompt, title=None):
    payload = {
        "prompt": prompt
    }

    def mcp_print(payload):
        print(json.dumps(payload, ensure_ascii=False))

    def mcp_print_text(text, data=None):
        payload = {"content": [{"type": "text", "text": text}]}
        if data is not None:
            payload["data"] = data
        mcp_print(payload)

    def normalize_content_items(content_items):
        normalized = []
        for item in content_items:
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            if "data" in normalized_item and "text" not in normalized_item and normalized_item.get("type") == "text":
                normalized_item["text"] = normalized_item["data"]
            normalized.append(normalized_item)
        return normalized

    def extract_content_items(result):
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            return None
        if isinstance(result.get("content"), list):
            return result["content"]
        if isinstance(result.get("data"), dict) and isinstance(result["data"].get("content"), list):
            return result["data"]["content"]
        return None

    def filter_display_content(content_items):
        filtered = []
        for item in content_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "image":
                filtered.append(item)
                continue
            if item.get("type") == "text":
                text = item.get("text")
                if text is None:
                    text = item.get("data")
                if isinstance(text, str) and not text.strip():
                    continue
            filtered.append(item)
        return filtered

    def extract_remote_image_urls(content_items):
        urls = []
        for item in content_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "image_url":
                continue
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                urls.append(url.strip())
        return urls

    def looks_like_dsl_text(text):
        if not isinstance(text, str):
            return False
        stripped = text.strip()
        if not stripped:
            return False
        lower = stripped.lower()
        if len(stripped) <= 20 and "dsl" in lower:
            return False
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    diagram_type = parsed.get("t", parsed.get("type"))
                    diagram_data = parsed.get("d", parsed.get("dsl", parsed.get("data")))
                    version = parsed.get("v")
                    if isinstance(diagram_data, str) and diagram_data.strip():
                        if version is not None and isinstance(diagram_type, str) and diagram_type.strip():
                            return True
                        if version is not None:
                            return True
                        if isinstance(diagram_type, str) and diagram_type.strip():
                            return True
                return False
            except Exception:
                pass
        dsl_markers = [
            "\"nodes\"",
            "\"edges\"",
            "\"cells\"",
            "\"root\"",
            "\"page\"",
            "\"diagram\"",
            "\"shape\"",
            "\"linkDataArray\"",
            "\"nodeDataArray\"",
        ]
        if "dsl" in lower and len(stripped) > 20:
            return True
        if any(marker in stripped for marker in dsl_markers) and len(stripped) > 40:
            return True
        mermaid_block_markers = (
            "graph ",
            "flowchart ",
            "sequencediagram",
            "classdiagram",
            "statediagram",
            "statediagram-v2",
            "erdiagram",
            "journey",
            "gantt",
            "mindmap",
            "timeline",
            "gitgraph",
            "pie",
            "quadrantchart",
            "requirementdiagram",
            "xychart",
            "block-beta",
        )
        content_lines = []
        for line in stripped.splitlines():
            normalized_line = line.strip()
            if not normalized_line:
                continue
            if normalized_line.startswith("%%"):
                continue
            content_lines.append(normalized_line)
        if content_lines:
            first_line = content_lines[0].lower()
            if first_line.startswith("smart "):
                return True
            if first_line.startswith(mermaid_block_markers):
                return True
        return False

    def has_dsl_content(content_items):
        for item in content_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if text is None:
                text = item.get("data")
            data_type = str(item.get("dataType", "")).lower()
            mime_type = str(item.get("mimeType", "")).lower()
            if data_type == "dsl":
                return True
            if mime_type in (
                "application/json",
                "application/dsl+json",
                "application/vnd.processon.dsl+json",
                "text/dsl",
            ):
                return True
            if looks_like_dsl_text(text):
                return True
        return False

    def extract_dsl_texts(content_items):
        dsl_texts = []
        seen = set()
        for item in content_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if text is None:
                text = item.get("data")
            if not isinstance(text, str):
                continue
            stripped = text.strip()
            if not stripped:
                continue
            data_type = str(item.get("dataType", "")).lower()
            mime_type = str(item.get("mimeType", "")).lower()
            is_dsl = data_type == "dsl" or mime_type in (
                "application/json",
                "application/dsl+json",
                "application/vnd.processon.dsl+json",
                "text/dsl",
            ) or looks_like_dsl_text(stripped)
            if not is_dsl:
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            dsl_texts.append(stripped)
        return dsl_texts

    def guess_code_fence(dsl_text):
        if not isinstance(dsl_text, str):
            return ""
        stripped = dsl_text.strip()
        if not stripped:
            return ""
        first_line = ""
        for line in stripped.splitlines():
            normalized = line.strip()
            if normalized:
                first_line = normalized.lower()
                break
        if first_line.startswith("smart "):
            return "text"
        if first_line.startswith((
            "graph ",
            "flowchart ",
            "sequencediagram",
            "classdiagram",
            "statediagram",
            "statediagram-v2",
            "erdiagram",
            "journey",
            "gantt",
            "mindmap",
            "timeline",
            "gitgraph",
            "pie",
            "quadrantchart",
            "requirementdiagram",
            "xychart",
            "block-beta",
        )):
            return "mermaid"
        if stripped.startswith("{") or stripped.startswith("["):
            return "json"
        return ""

    def build_dsl_copy_text_items(dsl_texts):
        items = []
        for index, dsl_text in enumerate(dsl_texts, start=1):
            fence = guess_code_fence(dsl_text)
            title = "\n".join([
                "已成功拿到 DSL 数据。复制 DSL 数据到 https://smart.processon.com/render-dsl 可以继续编辑。",
                "DSL 原文如下，可直接复制：",
            ])
            if len(dsl_texts) > 1:
                title = "\n".join([
                    "已成功拿到 DSL 数据。复制 DSL 数据到 https://smart.processon.com/render-dsl 可以继续编辑。",
                    f"DSL 原文 {index} 如下，可直接复制：",
                ])
            block = f"```{fence}\n{dsl_text}\n```" if fence else f"```\n{dsl_text}\n```"
            items.append({
                "type": "text",
                "text": "\n".join([title, block]),
            })
        return items

    def build_dsl_hint_text():
        return {
            "type": "text",
            "text": "已成功拿到 DSL 数据。复制 DSL 数据到 https://smart.processon.com/render-dsl 可以继续编辑。"
        }

    def build_remote_image_text(remote_image_urls):
        return {
            "type": "text",
            "text": "\n".join(["远程图片地址："] + remote_image_urls)
        }

    def build_saved_paths_text(save_result, saved_paths):
        return {
            "type": "text",
            "text": "\n".join([
                f"图片标题：{save_result['title']}",
                "图片已保存：",
            ] + saved_paths)
        }

    def normalize_bearer(api_key):
        if not api_key:
            return None
        api_key = api_key.strip()
        if not api_key:
            return None
        if api_key.lower().startswith("bearer "):
            return api_key
        return f"Bearer {api_key}"

    def build_headers(api_key):
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "ProcessOn-Architect-Skill/2.0"
        }
        bearer = normalize_bearer(api_key)
        if bearer:
            headers["Authorization"] = bearer
        return headers

    def do_request(headers):
        json_payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(API_URL, data=json_payload, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=180) as response:
            status_code = getattr(response, "status", "unknown")
            content_type = response.headers.get("Content-Type", "unknown")
            response_data = response.read().decode('utf-8')
            if not response_data.strip():
                raise ValueError(
                    f"Empty response body from ProcessOn API "
                    f"(status={status_code}, content_type={content_type})"
                )
            try:
                return json.loads(response_data)
            except json.JSONDecodeError as e:
                snippet = response_data[:500]
                raise ValueError(
                    f"Invalid JSON response from ProcessOn API "
                    f"(status={status_code}, content_type={content_type}, body_prefix={snippet!r})"
                ) from e

    def build_credential_metadata():
        macos_command = 'export PROCESSON_API_KEY="<your-processon-api-key>"'
        windows_powershell_command = '$env:PROCESSON_API_KEY="<your-processon-api-key>"'
        windows_cmd_command = 'set PROCESSON_API_KEY=<your-processon-api-key>'
        return {
            "credential": {
                "name": "PROCESSON_API_KEY",
                "label": "ProcessOn API Key",
                "kind": "secret",
                "required": True,
                "envVar": "PROCESSON_API_KEY",
                "placeholder": "<your-processon-api-key>",
                "description": "用于 ProcessOn API 回退模式的鉴权密钥。",
            },
            "actions": [
                {
                    "type": "request_credential",
                    "credential": "PROCESSON_API_KEY",
                    "label": "配置 ProcessOn API Key",
                    "mode": "secret",
                },
                {
                    "type": "show_config_example",
                    "target": "processon-api",
                    "label": "查看配置示例",
                },
                {
                    "type": "copy_command",
                    "label": "复制 macOS/Linux 配置命令",
                    "command": macos_command,
                    "platform": ["macos", "linux"],
                },
                {
                    "type": "copy_command",
                    "label": "复制 Windows PowerShell 配置命令",
                    "command": windows_powershell_command,
                    "platform": ["windows"],
                    "shell": "powershell",
                },
                {
                    "type": "copy_command",
                    "label": "复制 Windows CMD 配置命令",
                    "command": windows_cmd_command,
                    "platform": ["windows"],
                    "shell": "cmd",
                },
                {
                    "type": "retry",
                    "label": "配置完成后重试",
                },
            ],
            "suggestedCommands": {
                "macos_linux": macos_command,
                "windows_powershell": windows_powershell_command,
                "windows_cmd": windows_cmd_command,
                "verify": "echo $PROCESSON_API_KEY",
                "retryPrompt": "继续生成流程图",
            },
            "interactive": {
                "canRequestCredential": True,
                "preferredAction": "request_credential",
            },
        }

    def build_missing_api_key_payload():
        hint = "\n".join([
            "当前还没有检测到可用的 ProcessOn API Key，所以暂时无法直接生成图片版流程图。",
            "API Key/Token 获取地址：https://smart.processon.com/user",
            "macOS/Linux:",
            "  export PROCESSON_API_KEY=\"<your-processon-api-key>\"",
        ])
        payload = {
            "content": [{"type": "text", "text": hint}],
            "data": {
                "errorCode": "MISSING_API_KEY",
            },
        }
        payload["data"].update(build_credential_metadata())
        return payload

    def build_invalid_api_key_payload(http_message):
        hint = "\n".join([
            "检测到 PROCESSON_API_KEY，但鉴权失败。当前配置的 API Key 可能无效、已过期，或不适用于当前接口。",
            "如需重新获取 Token，请访问：https://smart.processon.com/user",
            "",
            f"失败原因：{http_message}",
            "",
            "请检查：",
            "1. PROCESSON_API_KEY 是否填写正确",
            "2. 该 Key 是否具备 smart.processon.com 接口访问权限",
            "3. 是否误填了其他系统的 token",
        ])
        payload = {
            "content": [{"type": "text", "text": hint}],
            "data": {
                "errorCode": "INVALID_API_KEY",
            },
        }
        payload["data"].update(build_credential_metadata())
        return payload

    api_key = os.environ.get("PROCESSON_API_KEY", "")

    try:
        if not api_key.strip():
            mcp_print(build_missing_api_key_payload())
            sys.exit(1)
        result = do_request(build_headers(api_key))
        content = extract_content_items(result)
        if content is not None:
            normalized_content = normalize_content_items(content)
            save_result = save_image_content(title, normalized_content)
            saved_paths = save_result["saved_paths"]
            remote_image_urls = extract_remote_image_urls(normalized_content)
            output_content = filter_display_content(normalized_content)
            has_dsl = has_dsl_content(normalized_content)
            dsl_texts = extract_dsl_texts(normalized_content)

            if has_dsl:
                output_content.extend(build_dsl_copy_text_items(dsl_texts))
            if remote_image_urls:
                output_content.append(build_remote_image_text(remote_image_urls))
            if saved_paths:
                output_content.append(build_saved_paths_text(save_result, saved_paths))

            output_payload = {"content": output_content}
            if isinstance(result, dict) and isinstance(result.get("data"), dict):
                output_payload["data"] = dict(result["data"])
            if remote_image_urls:
                if "data" not in output_payload:
                    output_payload["data"] = {}
                output_payload["data"]["remoteImageUrls"] = remote_image_urls
            if saved_paths:
                if "data" not in output_payload:
                    output_payload["data"] = {}
                output_payload["data"].update({
                    "imageTitle": save_result["title"],
                    "savedImagePaths": saved_paths,
                    "primarySavedImagePath": saved_paths[0],
                    "preferredDisplay": "inline",
                    "showInlineIfPossible": True,
                })
            mcp_print(output_payload)
            return

        raise ValueError("Invalid MCP response: missing 'content' array at top level or in 'data.content'")
            
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode('utf-8')
            msg = f"HTTP {e.code} {e.reason}: {body}"
        except:
            msg = f"HTTP {e.code} {e.reason}"
        current_api_key = os.environ.get("PROCESSON_API_KEY", "").strip()
        if e.code in (401, 403) and not current_api_key:
            missing_payload = build_missing_api_key_payload()
            missing_payload["content"][0]["text"] = f"{msg}\n\n{missing_payload['content'][0]['text']}"
            mcp_print(missing_payload)
            sys.exit(1)
        if e.code in (401, 403) and current_api_key:
            mcp_print(build_invalid_api_key_payload(msg))
            sys.exit(1)
        mcp_print_text(msg)
        sys.exit(1)
        
    except urllib.error.URLError as e:
        mcp_print_text(f"Connection Error: {e.reason}")
        sys.exit(1)
        
    except Exception as e:
        mcp_print_text(str(e))
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='ProcessOn AI Diagram Generator (Zero Dependency)')
    parser.add_argument('prompt', type=str, help='The optimized prompt for the diagram')
    parser.add_argument('--title', type=str, default='processon-diagram', help='Short title for the saved image filename')
    
    args = parser.parse_args()
    
    generate_diagram(args.prompt, args.title)
