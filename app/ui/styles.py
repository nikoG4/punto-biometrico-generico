APP_STYLE = """
QWidget {
    background: #111418;
    color: #eef2f5;
    font-family: Segoe UI, Arial;
    font-size: 16px;
}
QPushButton {
    background: #2563eb;
    border: 0;
    border-radius: 6px;
    padding: 10px 14px;
    font-weight: 600;
}
QPushButton:hover { background: #1d4ed8; }
QPushButton:disabled { background: #3b4250; color: #9ca3af; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #1b2028;
    border: 1px solid #384252;
    border-radius: 6px;
    padding: 9px;
}
QLabel#Title {
    font-size: 34px;
    font-weight: 700;
}
QLabel#StatusOk {
    color: #34d399;
    font-size: 28px;
    font-weight: 700;
}
QLabel#StatusError {
    color: #fb7185;
    font-size: 28px;
    font-weight: 700;
}
QLabel#Hint {
    color: #aeb7c4;
}
QFrame#Panel {
    background: #171b22;
    border: 1px solid #2a3240;
    border-radius: 8px;
}
"""
