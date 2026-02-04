import streamlit as st
import arxiv
import re

# --- 核心逻辑函数 (和之前类似) ---
def generate_bibtex_key(author_last_name, year, title):
    title_word = title.split()[0].lower()
    # 移除标题中可能的非字母字符
    title_word = re.sub(r'[^a-z]', '', title_word)
    return f"{author_last_name.lower()}{year}{title_word}"

def format_arxiv_to_bibtex(result):
    try:
        first_author_last = result.authors[0].name.split()[-1]
        year = result.published.year
        bib_key = generate_bibtex_key(first_author_last, year, result.title)
        authors_str = " and ".join([a.name for a.authors])
        
        # 构建 BibTeX
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
        return f"格式化出错: {str(e)}"

def search_arxiv(query):
    client = arxiv.Client()
    # 判断是否为 ID
    if re.match(r'\d{4}\.\d{4,5}', query):
        search = arxiv.Search(id_list=[query])
    else:
        search = arxiv.Search(query=query, max_results=1)

    try:
        result = next(client.results(search))
        return result, format_arxiv_to_bibtex(result)
    except StopIteration:
        return None, None
    except Exception as e:
        st.error(f"API 连接错误: {e}")
        return None, None

# --- 网页界面构建 (Streamlit) ---
st.set_page_config(page_title="论文 BibTeX 转换器", page_icon="📄")

st.title("📄 论文引用格式转换器")
st.markdown("输入 **arXiv ID** (如 `2110.14051`) 或 **论文标题**，自动生成标准 BibTeX。")

# 输入框
query = st.text_input("在此输入 ID 或 标题:", placeholder="例如: Attention Is All You Need 或 1706.03762")

if st.button("生成 BibTeX"):
    if not query:
        st.warning("请输入内容！")
    else:
        with st.spinner('正在去 arXiv 抓取数据...'):
            result_obj, bibtex_str = search_arxiv(query)
            
            if bibtex_str:
                st.success(f"找到论文: **{result_obj.title}**")
                
                # 显示代码块 (Streamlit 右上角自带复制按钮)
                st.code(bibtex_str, language='latex')
                
                # 额外信息展示
                with st.expander("查看论文详情"):
                    st.write(f"**发布时间:** {result_obj.published.date()}")
                    st.write(f"**摘要:** {result_obj.summary}")
            else:
                st.error("未找到相关论文，请检查 ID 或尝试更精确的标题。")

st.markdown("---")
st.caption("Data provided by arXiv API | Built with Streamlit")
