"""现有气泡使用的有界图片控件；只接收已校验字节，不接任意路径。"""
from PySide6.QtCore import QBuffer,QByteArray,QIODevice,QSize,Qt
from PySide6.QtGui import QImage,QMovie,QPixmap
from PySide6.QtWidgets import QLabel

class ExpressionImage(QLabel):
    def __init__(self,parent=None,maximum=180):
        super().__init__(parent);self.maximum=maximum;self.buffer=None;self.animation=None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
    def load(self,data,mime):
        self.stop()
        image=QImage.fromData(data)
        if image.isNull():self.setText('表情未能显示');return False
        size=image.size();size.scale(self.maximum,self.maximum,Qt.AspectRatioMode.KeepAspectRatio)
        self.setFixedSize(size)
        if mime in ('image/gif','image/webp'):
            movie=QMovie(self)
            self.buffer=QBuffer(movie);self.buffer.setData(QByteArray(data));self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            movie.setDevice(self.buffer);movie.setCacheMode(QMovie.CacheMode.CacheNone);movie.setScaledSize(size)
            if movie.isValid():
                self.animation=movie;self.setMovie(movie);movie.start();return True
            movie.deleteLater()
        self.setPixmap(QPixmap.fromImage(image.scaled(size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)))
        return True
    def stop(self):
        if self.animation:
            self.animation.stop();self.clear();self.animation.deleteLater();self.animation=None;self.buffer=None
        elif self.buffer:self.buffer.deleteLater();self.buffer=None
    def closeEvent(self,event):
        self.stop();super().closeEvent(event)
