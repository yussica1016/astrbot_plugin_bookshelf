# astrbot_plugin_bookshelf

AstrBot 书架插件。支持上传 TXT 书籍、按章节切分存储、阅读指定章节、查看目录、阅读进度、删除书籍、写笔记、看笔记、写读后感、看读后感、共读面板。

> 本仓库根据《AstrBot 书架插件开发教程 astrbot_plugin_bookshelf v2.0.0》整理实现。

## 安装

把本仓库放到 AstrBot 的插件目录：

```bash
cd /AstrBot/data/plugins
git clone https://github.com/你的用户名/astrbot_plugin_bookshelf.git
```

然后在 AstrBot WebUI 插件管理里重载插件，或重启 AstrBot。

## 文件结构

```text
astrbot_plugin_bookshelf/
├── metadata.yaml
├── main.py
├── requirements.txt
├── README.md
└── .gitignore
```

插件运行后会自动创建 `books/` 目录保存书籍数据。

## 指令

### 基础功能

```text
/上传书籍 书名 全文
/上传文本 书名
# 然后发送 txt 文件

/书架
/目录 书名
/读第 书名 第X章
/阅读进度 书名
/删除书籍 书名
```

### 笔记系统

```text
/写笔记 书名 第X章 内容
/看笔记 书名 第X章
/所有笔记 书名
```

### 读后感系统

```text
/读后感 书名 第X章 内容
/看读后感 书名 第X章
```

### 共读模式

```text
/共读 书名
```

## 说明

- 当前版本支持 TXT 上传。
- EPUB 请先在服务器或本地解析成 TXT 后再上传。
- 书籍数据默认保存在插件目录下的 `books/`。
