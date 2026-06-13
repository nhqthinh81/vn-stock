from setuptools import setup, find_packages

setup(
    name="vn-invest",
    version="1.0.0",
    description="Phân tích chứng khoán Việt Nam — Streamlit + vnstock",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "vnstock>=3.0.0",
        "streamlit>=1.35.0",
        "pandas>=2.0.0",
        "numpy>=1.26.0",
        "pyarrow>=14.0.0",
    ],
    entry_points={
        "console_scripts": [
            "vn-invest=vn_invest.cli:main",
        ],
    },
)
