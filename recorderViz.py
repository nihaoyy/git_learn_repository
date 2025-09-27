import struct
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QSpinBox, QTextEdit, QFileDialog, QMessageBox,
                             QGroupBox, QSplitter)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QObject
from PyQt6.QtGui import QIcon
import pyqtgraph as pg
from queue import Queue, Empty

class DataUnpacker:
    """
    数据解包器，负责将连续的二进制数据流解析为独立的数据帧
    每帧包含标识符、时间戳和实际数据
    """
    def __init__(self):
        # 帧头标识符
        self.frame_header = b'\xAA\xBB'
        self.header_len = 2
        self.min_frame_len = 8  # 最小帧长度：帧头(2) + 长度(2) + 标识符(2) + 时间戳(2)
        self.buffer = bytearray()
        
    def add_data(self, data):
        """添加新数据到缓冲区"""
        self.buffer.extend(data)
        
    def extract_frames(self):
        """从缓冲区中提取完整帧"""
        frames = []
        while len(self.buffer) >= self.min_frame_len:
            # 查找帧头
            header_pos = self.buffer.find(self.frame_header)
            if header_pos == -1:
                # 没有找到帧头，清空缓冲区
                self.buffer.clear()
                break
                
            if header_pos > 0:
                # 丢弃帧头之前的数据
                # Python 的切片语法，表示 “从列表开头（索引 0）到 header_pos 之前的所有元素”
                del self.buffer[:header_pos]
                
            if len(self.buffer) < self.min_frame_len:
                # 缓冲区数据不足，等待更多数据
                break
                
            # 解析帧长度
            frame_len = struct.unpack('>H', self.buffer[2:4])[0]
            
            if len(self.buffer) < frame_len:
                # 完整帧尚未接收完毕，等待更多数据
                break
                
            # 提取完整帧
            frame_data = self.buffer[:frame_len]
            del self.buffer[:frame_len]
            
            # 解析帧
            frame = self._parse_frame(frame_data)
            if frame:
                frames.append(frame)
                
        return frames
        
    # 将二进制数据流按固定格式解析为结构化的字典
    def _parse_frame(self, frame_data):
        """解析单个帧数据"""
        try:
            header = frame_data[:2]
            length = struct.unpack('>H', frame_data[2:4])[0]
            identifier = struct.unpack('>H', frame_data[4:6])[0]
            timestamp = struct.unpack('>H', frame_data[6:8])[0]
            data = frame_data[8:]
            
            return {
                'header': header,
                'length': length,
                'identifier': identifier,
                'timestamp': timestamp,
                'data': data
            }
        except Exception as e:
            print(f"解析帧时出错: {e}")
            return None

class DataParser:
    """
    数据解析器，能够根据数据描述信息从二进制数据中提取特定字段的值
    支持多种基础数据类型
    """
    def __init__(self):
        # 数据类型映射
        self.type_map = {
            'uint8': ('B', 1),
            'int8': ('b', 1),
            'uint16': ('>H', 2),
            'int16': ('>h', 2),
            'uint32': ('>I', 4),
            'int32': ('>i', 4),
            'float': ('>f', 4),
            'double': ('>d', 8)
        }
        
    def parse_data(self, binary_data, data_description):
        """
        根据数据描述信息解析二进制数据
        data_description: JSON格式的数据描述
        """
        result = {}    # 空字典，用于存储最终解析后的结构化数据
        offset = 0     # 标记当前解析到二进制数据的哪个位置
        
        try:
            for field in data_description.get('fields', []):     # 每个元素 field 是一个字典，代表一个要解析的字段
                field_name = field['name']
                field_type = field['type']
                
                if field_type not in self.type_map:
                    raise ValueError(f"不支持的数据类型: {field_type}")
                    
                format_str, size = self.type_map[field_type]
                
                if offset + size > len(binary_data):
                    raise ValueError(f"数据长度不足，无法解析字段: {field_name}")
                    
                value = struct.unpack(format_str, binary_data[offset:offset+size])[0]
                result[field_name] = value
                offset += size
                
            return result
        except Exception as e:
            print(f"解析数据时出错: {e}")
            return None

class DataSignals(QObject):
    """用于线程间通信的信号类"""
    data_received = pyqtSignal(object, int)

class HTTPDataHandler(BaseHTTPRequestHandler):
    """
    HTTP请求处理器，用于接收实时数据
    """
    def do_POST(self):
        """处理POST请求"""
        try:
            # 获取数据长度
            # self.headers 是一个字典，包含当前 HTTP 请求的所有头部信息
            # Content-Length 头部指定了 POST 请求体的字节长度，通过它可以确定需要读取多少数据
            content_length = int(self.headers['Content-Length'])

            # 读取数据
            # self.rfile 是一个类文件对象，代表读取请求体数据（二进制模式）
            post_data = self.rfile.read(content_length)
            
            # 将数据放入队列
            if hasattr(self.server, 'data_queue'):
                self.server.data_queue.put(post_data)
            
            # 发送响应
            self.send_response(200)
            # 发送响应头部信息：这里指定 Content-type 为 application/json，告诉客户端响应体是 JSON 格式
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "success"}')
        except Exception as e:
            print(f"处理POST请求时出错: {e}")
            self.send_response(400)
            self.end_headers()
            
    def do_GET(self):
        """处理GET请求 - RESTful API"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        if path == '/api/status':
            # 返回服务器状态
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            status = {
                "status": "running",
                "port": self.server.server_port if hasattr(self.server, 'server_port') else 0
            }
            self.wfile.write(json.dumps(status).encode())
        elif path == '/api/data':
            # 返回最新数据（示例）
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            data = {"message": "Data endpoint"}
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')
            
    def do_PUT(self):
        """处理PUT请求 - RESTful API"""
        self.do_POST()
        
    def log_message(self, format, *args):
        """重写日志消息方法以减少输出"""
        pass

class RealTimeDataProcessor:
    """
    实时数据处理器，通过HTTP服务接收数据流并更新UI
    """
    def __init__(self, signals):
        self.signals = signals
        self.data_queue = Queue()
        self.unpacker = DataUnpacker()
        self.parser = DataParser()
        self.data_description = None
        self.server = None
        self.server_thread = None
        self.running = False
        self.process_thread = None
        
    def set_data_description(self, description):
        """设置数据描述信息"""
        self.data_description = description
        
    def start_server(self, port=8080):
        """启动HTTP服务器, 监听指定端口"""
        if self.server_thread and self.server_thread.is_alive():
            return False
            
        try:
            self.server = HTTPServer(('localhost', port), HTTPDataHandler)
            self.server.data_queue = self.data_queue
            self.server.server_port = port
            self.running = True
            
            # 启动服务器线程处理HTTP请求
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()  
            
            # 启动数据处理线程
            self.process_thread = threading.Thread(target=self._process_data)
            self.process_thread.daemon = True
            self.process_thread.start()
            
            return True
        except Exception as e:
            print(f"启动服务器时出错: {e}")
            return False
            
    def stop_server(self):
        """停止HTTP服务器"""
        self.running = False
        if self.server:
            self.server.shutdown()
            self.server = None
        if self.server_thread:
            self.server_thread.join()
        if self.process_thread:
            self.process_thread.join()
            
    def _process_data(self):
        """处理接收到的数据"""
        while self.running:
            try:
                # 从队列获取数据
                data = self.data_queue.get(timeout=0.1)   # 持续检查是否有新数据到达（使用0.1秒超时）
                # 将接收到的数据添加到解包器缓冲区
                self.unpacker.add_data(data)
                # 提取完整数据帧
                frames = self.unpacker.extract_frames()
                
                # 处理每个帧
                for frame in frames:
                    if self.data_description:
                        # 解析数据
                        parsed_data = self.parser.parse_data(frame['data'], self.data_description)
                        if parsed_data:
                            # 如果成功解析，发送信号更新UI（线程安全）
                            self.signals.data_received.emit(parsed_data, frame['timestamp'])
                            
            except Empty:
                continue
            except Exception as e:
                print(f"处理数据时出错: {e}")

class OfflineDataProcessor:
    """
    离线数据处理器，解析JSON描述文件和二进制数据文件
    """
    def __init__(self):
        self.unpacker = DataUnpacker()
        self.parser = DataParser()
        
    def load_description(self, json_file):
        """加载数据描述文件"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载描述文件时出错: {e}")
            return None
            
    def process_binary_file(self, binary_file, description):
        """处理二进制数据文件"""
        try:
            with open(binary_file, 'rb') as f:
                binary_data = f.read()
                
            self.unpacker.add_data(binary_data)
            frames = self.unpacker.extract_frames()
            parsed_frames = []
            
            for frame in frames:
                parsed_data = self.parser.parse_data(frame['data'], description)
                if parsed_data:
                    parsed_frames.append({
                        'timestamp': frame['timestamp'],
                        'data': parsed_data
                    })
                    
            return parsed_frames
        except Exception as e:
            print(f"处理二进制文件时出错: {e}")
            return []

class MainWindow(QMainWindow):
    """
    主窗口类，使用PyQt6构建图形界面，使用pyqtgraph绘制曲线
    """
    def __init__(self):
        super().__init__()
        
        # 数据存储
        self.data_series = {}
        self.timestamps = []
        self.max_data_points = 1000
        
        # 组件
        self.signals = DataSignals()
        self.signals.data_received.connect(self.update_plot_data)
        self.real_time_processor = RealTimeDataProcessor(self.signals)
        self.offline_processor = OfflineDataProcessor()
        self.data_description = None
        
        # 定时器用于定期刷新图表
        self.plot_timer = QTimer()
        self.plot_timer.timeout.connect(self.update_plot)
        self.plot_timer.start(100)  # 每100ms刷新一次
        
        # 创建UI
        self.init_ui()
        
    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle('Recorder Visualizer')
        self.setGeometry(100, 100, 1200, 800)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建主布局
        main_layout = QVBoxLayout(central_widget)
        
        # 创建分割器
        splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(splitter)
        
        # 上半部分：控制面板
        control_widget = QWidget()
        control_layout = QVBoxLayout(control_widget)
        
        # 实时数据控制面板
        realtime_group = QGroupBox("实时数据控制")
        realtime_layout = QHBoxLayout(realtime_group)
        
        realtime_layout.addWidget(QLabel("端口:"))
        self.port_spinbox = QSpinBox()
        self.port_spinbox.setRange(1, 65535)
        self.port_spinbox.setValue(8080)
        realtime_layout.addWidget(self.port_spinbox)
        
        self.start_button = QPushButton("启动服务")
        self.start_button.clicked.connect(self.start_server)
        realtime_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("停止服务")
        self.stop_button.clicked.connect(self.stop_server)
        self.stop_button.setEnabled(False)
        realtime_layout.addWidget(self.stop_button)
        
        # 离线数据控制面板
        offline_group = QGroupBox("离线数据控制")
        offline_layout = QHBoxLayout(offline_group)
        
        self.load_desc_button = QPushButton("加载描述文件")
        self.load_desc_button.clicked.connect(self.load_description)
        offline_layout.addWidget(self.load_desc_button)
        
        self.load_data_button = QPushButton("加载数据文件")
        self.load_data_button.clicked.connect(self.load_binary_data)
        offline_layout.addWidget(self.load_data_button)
        
        # 状态标签
        self.status_label = QLabel("就绪")
        realtime_layout.addWidget(self.status_label)
        realtime_layout.addStretch()
        
        control_layout.addWidget(realtime_group)
        control_layout.addWidget(offline_group)
        
        # 数据描述显示
        self.desc_text = QTextEdit()
        self.desc_text.setMaximumHeight(150)
        control_layout.addWidget(QLabel("数据描述:"))
        control_layout.addWidget(self.desc_text)
        
        splitter.addWidget(control_widget)
        
        # 下半部分：图表
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_widget.setTitle("实时数据")
        self.plot_widget.setLabel('left', '数值')
        self.plot_widget.setLabel('bottom', '时间戳')
        splitter.addWidget(self.plot_widget)
        
        # 调整分割器比例
        splitter.setSizes([300, 500])
        
    def start_server(self):
        """启动HTTP服务"""
        port = self.port_spinbox.value()
        if self.real_time_processor.start_server(port):
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.status_label.setText(f"服务已在端口 {port} 启动")
        else:
            QMessageBox.critical(self, "错误", "无法启动服务")
            
    def stop_server(self):
        """停止HTTP服务"""
        self.real_time_processor.stop_server()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText("服务已停止")
        
    def load_description(self):
        """加载数据描述文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "选择数据描述文件", 
            "", 
            "JSON文件 (*.json);;所有文件 (*)"
        )
        
        if file_path:
            self.data_description = self.offline_processor.load_description(file_path)
            if self.data_description:
                self.desc_text.setPlainText(json.dumps(self.data_description, indent=2, ensure_ascii=False))
                self.real_time_processor.set_data_description(self.data_description)
                self.status_label.setText(f"已加载描述文件: {os.path.basename(file_path)}")
                
                # 初始化数据系列
                self.data_series = {}
                for field in self.data_description.get('fields', []):
                    self.data_series[field['name']] = []
                self.timestamps = []
                
                # 清空图表
                self.plot_widget.clear()
            else:
                QMessageBox.critical(self, "错误", "无法加载描述文件")
                
    def load_binary_data(self):
        """加载离线二进制数据文件"""
        if not self.data_description:
            QMessageBox.warning(self, "警告", "请先加载数据描述文件")
            return
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "选择数据文件", 
            "", 
            "二进制文件 (*.bin);;所有文件 (*)"
        )
        
        if file_path:
            self.status_label.setText("正在处理离线数据...")
            QApplication.processEvents()
            
            parsed_data = self.offline_processor.process_binary_file(file_path, self.data_description)
            if parsed_data:
                # 清空现有数据
                self.clear_plot_data()
                
                # 加载新数据
                for frame in parsed_data:
                    self.add_data_point(frame['data'], frame['timestamp'])
                    
                self.update_plot()
                self.status_label.setText(f"已加载数据文件: {os.path.basename(file_path)}, 共 {len(parsed_data)} 条记录")
            else:
                QMessageBox.critical(self, "错误", "无法处理数据文件")
                self.status_label.setText("处理离线数据失败")
                
    def clear_plot_data(self):
        """清空图表数据"""
        self.timestamps.clear()
        for series in self.data_series.values():
            series.clear()
            
    def add_data_point(self, data, timestamp):
        """添加数据点"""
        self.timestamps.append(timestamp)
        for key, value in data.items():
            if key not in self.data_series:
                self.data_series[key] = []
            self.data_series[key].append(value)
            
        # 限制数据点数量以提高性能
        if len(self.timestamps) > self.max_data_points:
            self.timestamps.pop(0)
            for series in self.data_series.values():
                if series:
                    series.pop(0)
                    
    def update_plot(self):
        """更新图表"""
        self.plot_widget.clear()
        
        # 为每个数据系列创建不同的颜色
        colors = ['r', 'g', 'b', 'c', 'm', 'y']
        for i, (name, series) in enumerate(self.data_series.items()):
            if series:
                color = colors[i % len(colors)]
                self.plot_widget.plot(self.timestamps, series, pen=pg.mkPen(color=color, width=2), name=name)
                
        # 添加图例
        self.plot_widget.addLegend()
        
    def update_plot_data(self, data, timestamp):
        """更新图表数据（由信号触发）"""
        self.add_data_point(data, timestamp)

def main():
    """主函数"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()