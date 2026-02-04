import streamlit as st
import arxiv
import re

# --- 核心逻辑函数 ---
def generate_bibtex_key(author_last_name, year, title):
    # 提取标题第一个单词并清理非字母字符
    first_word = title.split()[0].lower()
    clean_word = re.sub(r'[^a-z]', '', first_word)
    # 转换为类似 salehi2022unified 的格式
    return f"{author_last_name.lower().replace(' ', '')}{year}{clean_word}"

def format_arxiv_to_bibtex(result):
    try:
        # 修正后的作者提取逻辑
        authors_list = [a.name for a in result.authors]
        authors_str = " and ".join(authors_list)
        
        # 获取第一作者姓氏用于生成 Key
        first_author_last = authors_list[0].split()[-1]
        year = result.published.year
        bib_key = generate_bibtex_key(first_author_last, year, result.title)
        
        # 构建 BibTeX 字符串 (完全匹配你截图的格式)
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
    # 清理输入字符
    query = query.strip()
    
    # 识别 arXiv ID
    if re.match(r'\d{4}\.\d{4,5}', query):
        search = arxiv.Search(id_list=[query])
    else:
        search = arxiv.Search(query=query, max_results=1)

    try:
        results = list(client.results(search))
        if results:
            return results[0], format_arxiv_to_bibtex(results[0])
        return None, None
    except Exception as e:
        st.error(f"连接 arXiv 失败: {e}")
        return None, None

# --- Streamlit 网页界面 ---
st.set_page_config(page_title="BibTeX Converter", page_icon="📚")

st.title("📚 BibTeX 自动转换工具")
st.info("只需输入 arXiv ID 或 论文完整标题，即可生成截图中的标准格式。")

query = st.text_input("输入论文信息：", placeholder="例如: 2110.14051 或 Attention Is All You Need")

if st.button("开始转换"):
    if query:
        with st.spinner('正在调取 arXiv 数据...'):
            res_obj, bib_text = search_arxiv(query)
            if bib_text:
                st.success(f"成功找到：{res_obj.title}")
                # 使用 code 组件，方便一键复制
                st.code(bib_text, language='latex')
            else:
                st.error("未找到相关论文，请检查输入是否有误。")
    else:
        st.warning("请输入有效的内容后再点击。")

st.markdown("---")
st.caption("工具说明：本工具通过调用官方 arXiv API 获取实时数据。")
