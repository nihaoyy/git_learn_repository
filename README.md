# Recorder Visualizer

Recorder Visualizer 是一个上位机软件，用于接收、解析和可视化传感器数据。

## 功能特点

1. **数据解包器**：
   - 将连续的二进制数据流解析为独立的数据帧
   - 每帧包含标识符、时间戳和实际数据

2. **数据解析器**：
   - 根据JSON格式的数据描述信息从二进制数据中提取特定字段的值
   - 支持多种基础数据类型（uint8, int8, uint16, int16, uint32, int32, float, double）

3. **RESTful通信协议**：
   - 实现实时数据传输和保存
   - 提供标准的RESTful API接口

4. **图形界面**：
   - 使用PyQt6构建现代化图形界面
   - 使用pyqtgraph实现实时数据曲线绘制

5. **实时数据处理**：
   - 基于HTTP服务的数据接收机制
   - 处理高频率的数据流
   - 通过线程安全的方式更新UI

6. **离线数据处理**：
   - 解析JSON描述文件和二进制数据文件
   - 支持复杂的数据结构

7. **性能优化**：
   - 通过条件更新机制减少磁盘I/O操作
   - 实现稳定的数据解析与绘制功能

## 安装依赖

```bash
pip install PyQt6 pyqtgraph
```

## 使用方法

1. 运行程序：
   ```bash
   python recorderViz.py
   ```

2. 实时数据模式：
   - 在UI中设置端口号，点击"启动服务"按钮
   - 默认监听端口8080
   - 向 `http://localhost:8080` 发送POST请求发送二进制数据

3. 离线数据模式：
   - 点击"加载描述文件"加载JSON格式的数据描述文件
   - 点击"加载数据文件"加载二进制数据文件进行离线分析

## RESTful API 接口

### GET /api/status
获取服务器状态信息

### GET /api/data
获取最新数据（示例接口）

### POST /api/data
提交二进制数据

### PUT /api/data
更新数据（与POST功能相同）

## 数据格式

### JSON描述文件格式
```json
{
  "name": "sensor_data",
  "fields": [
    {
      "name": "temperature",
      "type": "float",
      "unit": "Celsius"
    },
    {
      "name": "humidity", 
      "type": "float",
      "unit": "%"
    }
  ]
}
```

### 二进制数据帧格式
```
帧头(2字节) | 长度(2字节) | 标识符(2字节) | 时间戳(2字节) | 数据(N字节)
 0xAA 0xBB
```