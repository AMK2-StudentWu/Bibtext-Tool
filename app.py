import streamlit as st
import arxiv
import re

# --- 核心逻辑函数 ---
def clean_query(text):
    """
    清洗输入的文本，尝试提取核心标题。
    例如：从 "[1] Salehi, M., ... A unified survey..." 中提取 "A unified survey..."
    """
    # 移除类似 [1] 或 1. 的序号
    text = re.sub(r'^\[\d+\]\s*|^\d+\.\s*', '', text)
    # 移除常见的作者年份括号，如 (2022) 或 [2022]
    text = re.sub(r'[\(\[]\d{4}[\)\]]', '', text)
    # 如果文本很长且包含逗号，尝试取后面部分（通常标题在作者列表后面）
    if len(text) > 100 and ',' in text:
        parts = text.split(',')
        # 寻找最长的那一段，通常就是标题
        return max(parts, key=len).strip()
    return text.strip()

def generate_bibtex_key(author_last_name, year, title):
    first_word = title.split()[0].lower()
    clean_word = re.sub(r'[^a-z]', '', first_word)
    return f"{author_last_name.lower().replace(' ', '')}{year}{clean_word}"

def format_arxiv_to_bibtex(result):
    try:
        authors_list = [a.name for a in result.authors]
        authors_str = " and ".join(authors_list)
        first_author_last = authors_list[0].split()[-1]
        year = result.published.year
        bib_key = generate_bibtex_key(first_author_last, year, result.title)
        
        # 构建 BibTeX 字符串
        bib_entry = f"""@misc{{{bib_key},
    title={{{result.title}}}, 
    author={{{authors_str}}},
    year={{{year}}},
    eprint={{{result.get_short_id()}}},
    archivePrefix={{arXiv}},
    primaryClass={{{result.primary_category}}},
    url={{{result.entry_id}}},
}}"""
        return bib_entry
    except Exception as e:
        return f"格式化解析出错: {str(e)}"

def search_arxiv(query):
    client = arxiv.Client()
    query = query.strip()
    
    # 1. 尝试直接作为 ID 搜索
    if re.match(r'\d{4}\.\d{4,5}', query):
        search = arxiv.Search(id_list=[query])
    else:
        # 2. 如果是文字，先进行清洗
        processed_query = clean_query(query)
        search = arxiv.Search(query=processed_query, max_results=1)

    try:
        results = list(client.results(search))
        if results:
            return results[0], format_arxiv_to_bibtex(results[0])
        return None, None
    except Exception as e:
        return None, None

# --- Streamlit 网页界面 ---
st.set_page_config(page_title="BibTeX Converter", page_icon="📚")

st.title("📚 BibTeX 自动转换工具")
st.markdown("""
**使用技巧：**
* 复制 **arXiv ID** (如 `2110.14051`) 结果最准确。
* 复制 **论文完整标题** 效果也很好。
* 避免输入包含大量作者名字的长段引用。
""")

query = st.text_area("输入论文信息（ID或标题）：", placeholder="例如: 2110.14051", height=100)

if st.button("开始转换"):
    if query:
        with st.spinner('正在检索数据库...'):
            res_obj, bib_text = search_arxiv(query)
            if bib_text:
                st.success(f"匹配成功：**{res_obj.title}**")
                st.code(bib_text, language='latex')
            else:
                st.error("抱歉，未能在 arXiv 数据库中匹配到该论文。建议只输入论文标题试试。")
    else:
        st.warning("请输入内容。")

st.markdown("---")
st.caption("Data: arXiv API | 保持 GitHub 更新即可自动同步网页")
