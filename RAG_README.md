# 🤖 PDFusion RAG + Web Research System

Hệ thống RAG (Retrieval-Augmented Generation) nâng cao cho PDFusion, kết hợp kiến thức từ PDF đã dịch với thông tin từ internet để cung cấp câu trả lời toàn diện và chính xác.

## 🌟 Tính năng chính

### 🔬 **Xử lý PDF Khoa học**
- **Layout Preservation**: Giữ nguyên cấu trúc tài liệu gốc
- **Multi-modal Content**: Xử lý text, equations, tables, figures
- **Smart Chunking**: Chia nhỏ nội dung theo ngữ cảnh
- **Metadata Extraction**: Trích xuất thông tin cấu trúc

### 🌐 **Web Research Integration**
- **Google Search**: Tìm kiếm thông tin bổ sung từ Google
- **Academic Sources**: Tích hợp Google Scholar, arXiv
- **Wikipedia**: Truy cập kiến thức từ Wikipedia
- **Source Validation**: Đánh giá độ tin cậy của nguồn

### 🎯 **Intelligent Q&A**
- **Cross-lingual**: Hỏi tiếng Việt, tìm trong PDF tiếng Anh
- **Context-aware**: Hiểu ngữ cảnh và liên kết thông tin
- **Reference Tracking**: Trích dẫn chính xác với navigation
- **Quality Metrics**: Đánh giá độ tin cậy câu trả lời

### 🇻🇳 **Vietnamese Optimization**
- **Vietnamese NLP**: Xử lý tiếng Việt chuyên biệt
- **Font Preservation**: Giữ nguyên font chữ Việt
- **Bilingual Responses**: Trả lời song ngữ khi cần

## 🏗️ Kiến trúc hệ thống

```
PDFusion RAG Architecture
├── 📄 Document Processing
│   ├── ScientificPDFProcessor    # Xử lý PDF khoa học
│   ├── EquationHandler          # Xử lý công thức toán
│   ├── TableExtractor           # Trích xuất bảng biểu
│   └── FigureProcessor          # Xử lý hình ảnh/đồ thị
├── 🗄️ Vector Storage
│   ├── ChromaDBManager          # Quản lý vector database
│   ├── EmbeddingManager         # Tạo embeddings đa ngôn ngữ
│   └── HybridSearch            # Tìm kiếm kết hợp
├── 🌐 Web Research
│   ├── SearchEngine            # Tìm kiếm đa nguồn
│   ├── ContentScraper          # Thu thập nội dung web
│   └── ReliabilityScorer       # Đánh giá độ tin cậy
├── 🤖 RAG Chain
│   ├── EnhancedRAGChain        # Chuỗi xử lý RAG chính
│   ├── ContextManager          # Quản lý ngữ cảnh
│   └── ResponseGenerator       # Tạo câu trả lời
└── 🖥️ GUI Integration
    ├── RAGChatPanel            # Giao diện chat
    ├── ReferenceManager        # Quản lý trích dẫn
    └── NavigationHandler       # Xử lý navigation
```

## 🚀 Cài đặt và sử dụng

### 1. **Cài đặt Dependencies**

```bash
# Cài đặt tất cả dependencies
pip install -r requirements.txt

# Hoặc cài đặt từng nhóm
pip install chromadb sentence-transformers langchain  # RAG core
pip install camelot-py pdfplumber pdf2image          # PDF processing
pip install googlesearch-python beautifulsoup4       # Web research
pip install spacy underthesea                        # NLP Vietnamese
```

### 2. **Cấu hình API Keys**

```bash
# Windows Command Prompt
set OPENAI_API_KEY=your_openai_key_here
set GEMINI_API_KEY=your_gemini_key_here

# Windows PowerShell
$env:OPENAI_API_KEY="your_openai_key_here"
$env:GEMINI_API_KEY="your_gemini_key_here"
```

### 3. **Chạy Demo**

```bash
# Kiểm tra hệ thống
python demo_rag.py

# Chạy ứng dụng chính
python main.py
```

## 📖 Hướng dẫn sử dụng

### **Bước 1: Load PDF**
1. Mở PDFusion
2. Click "Browse" hoặc "Open PDF"
3. Chọn file PDF cần dịch
4. Hệ thống sẽ tự động load vào panel trái

### **Bước 2: Dịch PDF**
1. Chọn ngôn ngữ nguồn và đích
2. Chọn dịch vụ (OpenAI/Gemini)
3. Click "Translate"
4. Đợi quá trình dịch hoàn thành
5. PDF đã dịch xuất hiện ở panel giữa

### **Bước 3: Sử dụng RAG Chat**
1. Panel bên phải là RAG Chat
2. Nhập câu hỏi bằng tiếng Việt
3. Bật/tắt "Tìm kiếm web" nếu cần
4. Click "Hỏi" hoặc nhấn Enter
5. Xem câu trả lời với trích dẫn

### **Bước 4: Navigation**
1. Click vào trích dẫn PDF → nhảy đến trang tương ứng
2. Click vào trích dẫn web → mở link trong browser
3. Sử dụng "Câu hỏi nhanh" cho các truy vấn phổ biến

## 💡 Ví dụ sử dụng

### **Câu hỏi mẫu:**

```
🔬 Khoa học/Kỹ thuật:
- "Giải thích thuật toán này hoạt động như thế nào?"
- "So sánh phương pháp này với các nghiên cứu khác"
- "Ứng dụng thực tế của công nghệ này là gì?"

📊 Phân tích dữ liệu:
- "Tóm tắt kết quả thí nghiệm trong bảng 3"
- "Ý nghĩa của biểu đồ ở trang 15 là gì?"
- "Mối quan hệ giữa các biến số được trình bày như thế nào?"

🌐 Nghiên cứu mở rộng:
- "Tìm thêm thông tin về chủ đề này trên internet"
- "Có nghiên cứu nào mới hơn về vấn đề này không?"
- "So sánh với tiêu chuẩn quốc tế hiện tại"
```

### **Kết quả mẫu:**

```
🤖 Trả lời:
Dựa trên tài liệu PDF và thông tin từ internet, thuật toán machine learning 
được mô tả trong tài liệu hoạt động theo nguyên lý...

📚 Tài liệu tham khảo:
📄 Nguồn từ PDF:
  • Trang 23: "The algorithm utilizes a neural network architecture..."
  • Trang 45: "Experimental results show 95% accuracy..."

🌐 Nguồn từ Internet:
  • Wikipedia: "Machine learning algorithms are computational methods..."
  • arXiv: "Recent advances in neural network architectures (2024)"

📊 Chất lượng - Độ tin cậy: 92%, Độ đầy đủ: 88%
```

## ⚙️ Cấu hình nâng cao

### **Vector Database Settings**
```python
# Trong code hoặc config file
vector_store = ChromaDBManager(
    persist_directory="./custom_db_path",
    embedding_model="paraphrase-multilingual-MiniLM-L12-v2"
)
```

### **Web Research Settings**
```python
web_research = WebResearchEngine(
    max_sources_per_query=5,
    reliability_threshold=0.6,
    enable_academic_search=True
)
```

### **RAG Chain Settings**
```python
rag_chain = EnhancedRAGChain(
    max_pdf_sources=5,
    max_web_sources=3,
    confidence_threshold=0.7
)
```

## 🔧 Troubleshooting

### **Lỗi thường gặp:**

#### **1. ChromaDB không khởi tạo được**
```
❌ Error: ChromaDB not available
✅ Solution: pip install chromadb
```

#### **2. Không tìm kiếm web được**
```
❌ Error: Google search failed
✅ Solution: Kiểm tra kết nối internet và cài đặt googlesearch-python
```

#### **3. Embedding model không load được**
```
❌ Error: SentenceTransformers not available
✅ Solution: pip install sentence-transformers
```

#### **4. PDF processing lỗi**
```
❌ Error: Table extraction failed
✅ Solution: pip install camelot-py[cv]
```

### **Performance Optimization:**

```python
# Tăng tốc độ xử lý
- Giảm số lượng sources: max_pdf_sources=3, max_web_sources=2
- Sử dụng embedding model nhỏ hơn: "all-MiniLM-L6-v2"
- Tắt web research cho câu hỏi đơn giản
- Cache kết quả cho câu hỏi lặp lại
```

## 📊 Metrics và Monitoring

### **Quality Metrics:**
- **Confidence Score**: Độ tin cậy câu trả lời (0-100%)
- **Completeness**: Độ đầy đủ thông tin (0-100%)
- **Source Diversity**: Đa dạng nguồn thông tin
- **Response Time**: Thời gian xử lý

### **Usage Statistics:**
- Số lượng câu hỏi đã xử lý
- Tỷ lệ sử dụng PDF vs Web sources
- Độ hài lòng người dùng (qua feedback)
- Performance benchmarks

## 🔮 Tính năng tương lai

### **Planned Features:**
- [ ] **Voice Input**: Hỏi đáp bằng giọng nói
- [ ] **Multi-document RAG**: Hỏi đáp trên nhiều PDF cùng lúc
- [ ] **Custom Knowledge Base**: Tạo knowledge base riêng
- [ ] **Advanced Analytics**: Phân tích sâu hơn về usage patterns
- [ ] **API Integration**: REST API cho integration
- [ ] **Mobile Support**: Phiên bản mobile app

### **Research Directions:**
- [ ] **Multimodal RAG**: Xử lý images, videos
- [ ] **Real-time Learning**: Học từ feedback người dùng
- [ ] **Domain Adaptation**: Tối ưu cho từng lĩnh vực cụ thể
- [ ] **Federated Learning**: Học phân tán từ nhiều users

## 🤝 Đóng góp

### **Cách đóng góp:**
1. Fork repository
2. Tạo feature branch
3. Implement tính năng mới
4. Viết tests
5. Submit pull request

### **Areas cần hỗ trợ:**
- Vietnamese NLP improvements
- Additional web sources integration
- Performance optimization
- UI/UX enhancements
- Documentation translation

## 📞 Hỗ trợ

### **Liên hệ:**
- **Issues**: Tạo issue trên GitHub
- **Discussions**: GitHub Discussions
- **Email**: support@pdfusion.com (if available)

### **Resources:**
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [LangChain Documentation](https://python.langchain.com/)
- [Sentence Transformers](https://www.sbert.net/)
- [Vietnamese NLP Resources](https://github.com/undertheseanlp)

---

**Made with ❤️ for Vietnamese developers and researchers**

*PDFusion RAG System - Bridging the gap between document knowledge and web intelligence*
