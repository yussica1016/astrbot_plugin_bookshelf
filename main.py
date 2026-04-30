import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, register, StarTools


def _safe_name(name: str) -> str:
    """把书名转换成安全目录名，避免路径穿越。"""
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = name.replace("..", "_")
    return name[:80] or "未命名书籍"


# 预编译正则，避免每次调用重复编译
_RE_CHAPTER = re.compile(
    r"(?m)^\s*((?:第\s*[一二三四五六七八九十百千万零〇0-9]+\s*[章节卷回篇幕].*)|(?:CHAPTER\s+[0-9IVXLCDM]+.*))\s*$",
    re.IGNORECASE,
)
_RE_NORMALIZE = re.compile(r"\n{3,}")
_RE_READ_CHAPTER = re.compile(r"^/?读第\s+(.+?)\s+第?\s*(\d+)\s*章?$")
_RE_WRITE_NOTE = re.compile(r"^/?写笔记\s+(.+?)\s+第?\s*(\d+)\s*章\s+(.+)$", re.S)
_RE_READ_NOTES = re.compile(r"^/?看笔记\s+(.+?)\s+第?\s*(\d+)\s*章?$")
_RE_WRITE_THOUGHT = re.compile(r"^/?读后感\s+(.+?)\s+第?\s*(\d+)\s*章\s+(.+)$", re.S)
_RE_READ_THOUGHTS = re.compile(r"^/?看读后感\s+(.+?)(?:\s+第?\s*(\d+)\s*章?)?$")


def _ensure_dir(data_dir: str) -> None:
    os.makedirs(data_dir, exist_ok=True)


def _book_dir(data_dir: str, book_name: str) -> str:
    return os.path.join(data_dir, _safe_name(book_name))


def _index_path(data_dir: str, book_name: str) -> str:
    return os.path.join(_book_dir(data_dir, book_name), "index.json")


def _notes_path(data_dir: str, book_name: str) -> str:
    return os.path.join(_book_dir(data_dir, book_name), "notes.json")


def _thoughts_path(data_dir: str, book_name: str) -> str:
    return os.path.join(_book_dir(data_dir, book_name), "thoughts.json")


def _chapter_path(data_dir: str, book_name: str, chapter_no: int) -> str:
    return os.path.join(_book_dir(data_dir, book_name), f"chapter_{chapter_no:04d}.txt")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"bookshelf: failed to load json {path}: {exc}")
        return default


def _save_json(path: str, data: Any) -> None:
    """原子写入：先写临时文件再rename，防止写入中途崩溃导致数据损坏"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _RE_NORMALIZE.sub("\n\n", text)
    return text.strip()


def _split_chapters(text: str) -> List[Tuple[str, str]]:
    """按常见中文/英文章节标题切分；找不到章节时按约 3000 字切。"""
    text = _normalize_text(text)
    if not text:
        return []

    matches = list(_RE_CHAPTER.finditer(text))

    chapters: List[Tuple[str, str]] = []
    if matches:
        for i, m in enumerate(matches):
            title = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            chapters.append((title, body or title))
        return chapters

    chunk_size = 3000
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size].strip()
        if chunk:
            chapters.append((f"第 {len(chapters) + 1} 章", chunk))
    return chapters


def _save_book(data_dir: str, book_name: str, text: str) -> Dict[str, Any]:
    _ensure_dir(data_dir)
    book_dir = _book_dir(data_dir, book_name)
    os.makedirs(book_dir, exist_ok=True)

    chapters = _split_chapters(text)
    if not chapters:
        raise ValueError("文本为空，无法保存。")

    chapter_meta = []
    for idx, (title, content) in enumerate(chapters, start=1):
        with open(_chapter_path(data_dir, book_name, idx), "w", encoding="utf-8") as f:
            f.write(content)
        chapter_meta.append({"no": idx, "title": title, "chars": len(content)})

    index = {
        "name": book_name.strip(),
        "safe_name": _safe_name(book_name),
        "created_at": _now(),
        "updated_at": _now(),
        "current_chapter": 1,
        "total_chapters": len(chapters),
        "chapters": chapter_meta,
    }
    _save_json(_index_path(data_dir, book_name), index)
    if not os.path.exists(_notes_path(data_dir, book_name)):
        _save_json(_notes_path(data_dir, book_name), [])
    if not os.path.exists(_thoughts_path(data_dir, book_name)):
        _save_json(_thoughts_path(data_dir, book_name), [])
    return index


def _load_index(data_dir: str, book_name: str) -> Optional[Dict[str, Any]]:
    path = _index_path(data_dir, book_name)
    if not os.path.exists(path):
        return None
    return _load_json(path, None)


def _list_books(data_dir: str) -> List[Dict[str, Any]]:
    _ensure_dir(data_dir)
    books = []
    for dirname in sorted(os.listdir(data_dir)):
        path = os.path.join(data_dir, dirname, "index.json")
        if os.path.exists(path):
            data = _load_json(path, None)
            if data:
                books.append(data)
    return books


def _author_name(event: AstrMessageEvent) -> str:
    uid = str(event.get_sender_id())
    owner_uid = os.environ.get("BOOKSHELF_OWNER_UID", "")
    if uid == owner_uid and owner_uid:
        return os.environ.get("BOOKSHELF_OWNER_NAME", "主人")
    return event.get_sender_name() or uid


def _format_chapter_preview(data_dir: str, book_name: str, chapter_no: int, limit: int = 3500) -> str:
    index = _load_index(data_dir, book_name)
    if not index:
        return f"没找到《{book_name}》。"
    total = int(index.get("total_chapters", 0))
    if chapter_no < 1 or chapter_no > total:
        return f"章节不存在。《{book_name}》共有 {total} 章。"

    path = _chapter_path(data_dir, book_name, chapter_no)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    title = index["chapters"][chapter_no - 1].get("title", f"第 {chapter_no} 章")
    index["current_chapter"] = chapter_no
    index["updated_at"] = _now()
    _save_json(_index_path(data_dir, book_name), index)

    suffix = "" if len(content) <= limit else f"\n\n……本章较长，已截断显示前 {limit} 字。"
    return f"《{book_name}》\n{title}\n\n{content[:limit]}{suffix}"


@register("astrbot_plugin_bookshelf", "沈砚清", "书架插件", "2.0.0", "https://github.com/yussica1016/astrbot_plugin_bookshelf")
class BookshelfPlugin(Star):
    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.config = config or {}
        self._pending_uploads: Dict[str, Tuple[str, float]] = {}  # uid -> (book_name, timestamp)
        self.data_dir = str(StarTools.get_data_dir(self.name))
        os.makedirs(self.data_dir, exist_ok=True)

    def _cleanup_stale_uploads(self, max_age: int = 300):
        """清理超过 max_age 秒的待上传记录，防止内存泄漏"""
        now = time.time()
        stale = [uid for uid, (_, ts) in self._pending_uploads.items() if now - ts > max_age]
        for uid in stale:
            del self._pending_uploads[uid]

    @filter.command("上传书籍")
    async def upload_book_text(self, event: AstrMessageEvent, book_name: str, content: str):
        """上传书籍全文：/上传书籍 书名 全文"""
        try:
            index = _save_book(self.data_dir, book_name, content)
            yield event.plain_result(
                f"已保存《{book_name}》。\n共 {index['total_chapters']} 章。\n可以用 /目录 {book_name} 查看目录。"
            )
        except Exception as exc:
            logger.exception("bookshelf: upload_book_text failed")
            yield event.plain_result(f"上传失败：{exc}")

    @filter.command("上传文本")
    async def wait_text_file(self, event: AstrMessageEvent, book_name: str):
        """先登记书名，然后下一条消息发送 txt 文件。"""
        uid = str(event.get_sender_id())
        self._pending_uploads[uid] = (book_name.strip(), time.time())
        yield event.plain_result(f"好，把《{book_name}》的 .txt 文件发过来。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def receive_file(self, event: AstrMessageEvent):
        uid = str(event.get_sender_id())
        # 提前return：只在有待上传记录时才继续处理，避免每条消息都走后续逻辑
        if uid not in self._pending_uploads:
            return
        self._cleanup_stale_uploads()
        if uid not in self._pending_uploads:
            return

        file_comp = None
        for comp in getattr(event.message_obj, "message", []) or []:
            if isinstance(comp, File):
                file_comp = comp
                break
        if file_comp is None:
            return

        # 文件类型检查
        filename = getattr(file_comp, "file", "") or getattr(file_comp, "name", "") or ""
        if not filename.lower().endswith(".txt"):
            yield event.plain_result("只支持 .txt 格式的文本文件。")
            return

        book_name, _ = self._pending_uploads.pop(uid)
        try:
            file_path = await file_comp.get_file()
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            index = _save_book(self.data_dir, book_name, text)
            yield event.plain_result(f"已导入《{book_name}》。共 {index['total_chapters']} 章。")
        except Exception as exc:
            logger.exception("bookshelf: receive_file failed")
            yield event.plain_result(f"文件导入失败：{exc}")

    @filter.command("书架", alias={"/书架"})
    async def list_books(self, event: AstrMessageEvent):
        books = _list_books(self.data_dir)
        if not books:
            yield event.plain_result("书架还是空的。可以用 /上传文本 书名 上传 txt。")
            return
        lines = ["我的书架："]
        for b in books:
            lines.append(
                f"- 《{b.get('name')}》：{b.get('total_chapters', 0)} 章，当前第 {b.get('current_chapter', 1)} 章"
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("目录")
    async def catalog(self, event: AstrMessageEvent, book_name: str):
        index = _load_index(self.data_dir, book_name)
        if not index:
            yield event.plain_result(f"没找到《{book_name}》。")
            return
        lines = [f"《{book_name}》目录："]
        for ch in index.get("chapters", []):
            lines.append(f"{ch['no']}. {ch.get('title', '')}（{ch.get('chars', 0)}字）")
        yield event.plain_result("\n".join(lines[:120]))

    @filter.regex(r"^/?读第\s+(.+?)\s+第?\s*(\d+)\s*章?$")
    async def read_chapter(self, event: AstrMessageEvent):
        raw = event.message_str.strip()
        m = re.match(r"^/?读第\s+(.+?)\s+第?\s*(\d+)\s*章?$", raw)
        if not m:
            return
        book_name = m.group(1).strip()
        chapter_no = int(m.group(2))
        yield event.plain_result(_format_chapter_preview(self.data_dir, book_name, chapter_no))

    @filter.command("阅读进度")
    async def progress(self, event: AstrMessageEvent, book_name: str):
        index = _load_index(self.data_dir, book_name)
        if not index:
            yield event.plain_result(f"没找到《{book_name}》。")
            return
        current = int(index.get("current_chapter", 1))
        total = int(index.get("total_chapters", 0))
        percent = 0 if total == 0 else current / total * 100
        yield event.plain_result(f"《{book_name}》阅读进度：第 {current}/{total} 章，约 {percent:.1f}%。")

    @filter.command("删除书籍")
    async def delete_book(self, event: AstrMessageEvent, book_name: str):
        import shutil

        path = _book_dir(self.data_dir, book_name)
        if not os.path.exists(path):
            yield event.plain_result(f"没找到《{book_name}》。")
            return
        shutil.rmtree(path)
        yield event.plain_result(f"已删除《{book_name}》。")

    @filter.regex(r"^/?写笔记\s+(.+?)\s+第?\s*(\d+)\s*章\s+(.+)$")
    async def write_note(self, event: AstrMessageEvent):
        raw = event.message_str.strip()
        m = re.match(r"^/?写笔记\s+(.+?)\s+第?\s*(\d+)\s*章\s+(.+)$", raw, re.S)
        if not m:
            return
        book_name, chapter_no, content = m.group(1).strip(), int(m.group(2)), m.group(3).strip()
        if not _load_index(self.data_dir, book_name):
            yield event.plain_result(f"没找到《{book_name}》。")
            return
        notes = _load_json(_notes_path(self.data_dir, book_name), [])
        notes.append({"author": _author_name(event), "chapter": chapter_no, "content": content, "time": _now()})
        _save_json(_notes_path(self.data_dir, book_name), notes)
        yield event.plain_result(f"已记录《{book_name}》第 {chapter_no} 章笔记。")

    @filter.regex(r"^/?看笔记\s+(.+?)\s+第?\s*(\d+)\s*章?$")
    async def read_notes(self, event: AstrMessageEvent):
        raw = event.message_str.strip()
        m = re.match(r"^/?看笔记\s+(.+?)\s+第?\s*(\d+)\s*章?$", raw)
        if not m:
            return
        book_name, chapter_no = m.group(1).strip(), int(m.group(2))
        notes = [n for n in _load_json(_notes_path(self.data_dir, book_name), []) if int(n.get("chapter", 0)) == chapter_no]
        if not notes:
            yield event.plain_result(f"《{book_name}》第 {chapter_no} 章还没有笔记。")
            return
        lines = [f"《{book_name}》第 {chapter_no} 章笔记："]
        for n in notes[-20:]:
            lines.append(f"- {n.get('author')}｜{n.get('time')}\n  {n.get('content')}")
        yield event.plain_result("\n".join(lines))

    @filter.command("所有笔记")
    async def all_notes(self, event: AstrMessageEvent, book_name: str):
        notes = _load_json(_notes_path(self.data_dir, book_name), [])
        if not notes:
            yield event.plain_result(f"《{book_name}》还没有笔记。")
            return
        lines = [f"《{book_name}》全部笔记："]
        for n in notes[-50:]:
            lines.append(f"- 第 {n.get('chapter')} 章｜{n.get('author')}｜{n.get('time')}\n  {n.get('content')}")
        yield event.plain_result("\n".join(lines))

    @filter.regex(r"^/?读后感\s+(.+?)\s+第?\s*(\d+)\s*章\s+(.+)$")
    async def write_thought(self, event: AstrMessageEvent):
        raw = event.message_str.strip()
        m = re.match(r"^/?读后感\s+(.+?)\s+第?\s*(\d+)\s*章\s+(.+)$", raw, re.S)
        if not m:
            return
        book_name, chapter_no, content = m.group(1).strip(), int(m.group(2)), m.group(3).strip()
        if not _load_index(self.data_dir, book_name):
            yield event.plain_result(f"没找到《{book_name}》。")
            return
        thoughts = _load_json(_thoughts_path(self.data_dir, book_name), [])
        thoughts.append({"author": _author_name(event), "chapter": chapter_no, "content": content, "time": _now()})
        _save_json(_thoughts_path(self.data_dir, book_name), thoughts)
        yield event.plain_result(f"已记录《{book_name}》第 {chapter_no} 章读后感。")

    @filter.regex(r"^/?看读后感\s+(.+?)(?:\s+第?\s*(\d+)\s*章?)?$")
    async def read_thoughts(self, event: AstrMessageEvent):
        raw = event.message_str.strip()
        m = re.match(r"^/?看读后感\s+(.+?)(?:\s+第?\s*(\d+)\s*章?)?$", raw)
        if not m:
            return
        book_name = m.group(1).strip()
        chapter_raw = m.group(2)
        thoughts = _load_json(_thoughts_path(self.data_dir, book_name), [])
        if chapter_raw:
            chapter_no = int(chapter_raw)
            thoughts = [t for t in thoughts if int(t.get("chapter", 0)) == chapter_no]
        if not thoughts:
            yield event.plain_result(f"《{book_name}》还没有对应读后感。")
            return
        lines = [f"《{book_name}》读后感："]
        for t in thoughts[-20:]:
            lines.append(f"- 第 {t.get('chapter')} 章｜{t.get('author')}｜{t.get('time')}\n  {t.get('content')}")
        yield event.plain_result("\n".join(lines))

    @filter.command("共读")
    async def shared_panel(self, event: AstrMessageEvent, book_name: str):
        index = _load_index(self.data_dir, book_name)
        if not index:
            yield event.plain_result(f"没找到《{book_name}》。")
            return
        notes = _load_json(_notes_path(self.data_dir, book_name), [])
        thoughts = _load_json(_thoughts_path(self.data_dir, book_name), [])
        current = int(index.get("current_chapter", 1))
        total = int(index.get("total_chapters", 0))
        next_ch = min(current + 1, total) if total else 1
        recent_note = notes[-1] if notes else None
        recent_thought = thoughts[-1] if thoughts else None
        lines = [
            f"《{book_name}》共读面板",
            f"进度：第 {current}/{total} 章",
            f"笔记数：{len(notes)}",
            f"读后感数：{len(thoughts)}",
        ]
        if recent_note:
            lines.append(f"最近笔记：第 {recent_note.get('chapter')} 章｜{recent_note.get('author')}：{recent_note.get('content')}")
        if recent_thought:
            lines.append(f"最近读后感：第 {recent_thought.get('chapter')} 章｜{recent_thought.get('author')}：{recent_thought.get('content')}")
        lines.append(f"下一章建议：/读第 {book_name} 第{next_ch}章")
        yield event.plain_result("\n".join(lines))
