from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "seapath_native",
        [
            "backend/native/bindings.cpp",
            "backend/native/router.cpp",
        ],
        cxx_std=17,
    ),
]

setup(
    name="seapath_native",
    version="1.0.0",
    description="SeaPath C++ native routing acceleration module",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
