import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

def make_install():
	print("pre-build : make install")
	subprocess.run(
		["make", "install"],
		cwd=PROJECT_ROOT,
		capture_output=True
	)

def make_build_frontend():
	print("pre-build : make build-frontend")
	subprocess.run(
		["make", "build-frontend"],
		cwd=PROJECT_ROOT,
		capture_output=True
	)

if __name__ == "__main__":
	make_install()
	make_build_frontend()
