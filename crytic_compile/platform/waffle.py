"""
Waffle platform
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from crytic_compile.compilation_unit import CompilationUnit
from crytic_compile.compiler.compiler import CompilerVersion
from crytic_compile.platform.abstract_platform import AbstractPlatform
from crytic_compile.platform.exceptions import InvalidCompilation
from crytic_compile.platform.types import Type
from crytic_compile.utils.naming import convert_filename

# Handle cycle
from crytic_compile.utils.natspec import Natspec

if TYPE_CHECKING:
    from crytic_compile import CryticCompile

LOGGER = logging.getLogger("CryticCompile")


class Waffle(AbstractPlatform):
    """
    Waffle platform
    """

    NAME = "Waffle"
    PROJECT_URL = "https://github.com/EthWorks/Waffle"
    TYPE = Type.WAFFLE

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def compile(self, crytic_compile: "CryticCompile", **kwargs: str) -> None:
        """
        Compile the target

        :param crytic_compile:
        :param target:
        :param kwargs:
        :return:
        """

        waffle_ignore_compile = kwargs.get("waffle_ignore_compile", False) or kwargs.get(
            "ignore_compile", False
        )
        target = self._target

        cmd = ["waffle"]
        if not kwargs.get("npx_disable", False):
            cmd = ["npx"] + cmd

        # Default behaviour (without any config_file)
        build_directory = os.path.join("build")
        compiler = "native"
        config: Dict = dict()

        config_file = kwargs.get("waffle_config_file", "waffle.json")

        potential_config_files = list(Path(target).rglob("*waffle*.json"))
        if potential_config_files and len(potential_config_files) == 1:
            config_file = str(potential_config_files[0])

        # Read config file
        if config_file:
            config = _load_config(config_file)

            # old version
            if "compiler" in config:
                compiler = config["compiler"]
            if "compilerType" in config:
                compiler = config["compilerType"]

            if "compilerVersion" in config:
                version = config["compilerVersion"]
            else:
                version = _get_version(compiler, target, config=config)

            if "targetPath" in config:
                build_directory = config["targetPath"]

        else:
            version = _get_version(compiler, target)

        if "outputType" not in config or config["outputType"] != "all":
            config["outputType"] = "all"

        needed_config = {
            "compilerOptions": {
                "outputSelection": {
                    "*": {
                        "*": [
                            "evm.bytecode.object",
                            "evm.deployedBytecode.object",
                            "abi",
                            "evm.bytecode.sourceMap",
                            "evm.deployedBytecode.sourceMap",
                        ],
                        "": ["ast"],
                    }
                }
            }
        }

        # Set the config as it should be
        if "compilerOptions" in config:
            curr_config: Dict = config["compilerOptions"]
            curr_needed_config: Dict = needed_config["compilerOptions"]
            if "outputSelection" in curr_config:
                curr_config = curr_config["outputSelection"]
                curr_needed_config = curr_needed_config["outputSelection"]
                if "*" in curr_config:
                    curr_config = curr_config["*"]
                    curr_needed_config = curr_needed_config["*"]
                    if "*" in curr_config:
                        curr_config["*"] += curr_needed_config["*"]
                    else:
                        curr_config["*"] = curr_needed_config["*"]

                    if "" in curr_config:
                        curr_config[""] += curr_needed_config[""]
                    else:
                        curr_config[""] = curr_needed_config[""]

                else:
                    curr_config["*"] = curr_needed_config["*"]

            else:
                curr_config["outputSelection"] = curr_needed_config["outputSelection"]
        else:
            config["compilerOptions"] = needed_config["compilerOptions"]

        if not waffle_ignore_compile:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=target) as file_desc:
                json.dump(config, file_desc)
                file_desc.flush()

                # cmd += [os.path.relpath(file_desc.name)]
                cmd += [Path(file_desc.name).name]

                LOGGER.info("Temporary file created: %s", file_desc.name)
                LOGGER.info("'%s running", " ".join(cmd))

                try:
                    with subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=target
                    ) as process:
                        stdout, stderr = process.communicate()
                        if stdout:
                            LOGGER.info(stdout.decode())
                        if stderr:
                            LOGGER.error(stderr.decode())
                except OSError as error:
                    # pylint: disable=raise-missing-from
                    raise InvalidCompilation(error)

        if not os.path.isdir(os.path.join(target, build_directory)):
            raise InvalidCompilation("`waffle` compilation failed: build directory not found")

        combined_path = os.path.join(target, build_directory, "Combined-Json.json")
        if not os.path.exists(combined_path):
            raise InvalidCompilation("`Combined-Json.json` not found")

        with open(combined_path, "r") as file_desc:
            target_all = json.load(file_desc)

        optimized = None

        compilation_unit = CompilationUnit(crytic_compile, str(target))

        for contract in target_all["contracts"]:
            target_loaded = target_all["contracts"][contract]
            contract = contract.split(":")
            filename = convert_filename(
                contract[0], _relative_to_short, crytic_compile, working_dir=target
            )

            contract_name = contract[1]

            compilation_unit.asts[filename.absolute] = target_all["sources"][contract[0]]["AST"]
            crytic_compile.filenames.add(filename)
            compilation_unit.contracts_filenames[contract_name] = filename
            compilation_unit.contracts_names.add(contract_name)
            compilation_unit.abis[contract_name] = target_loaded["abi"]

            userdoc = target_loaded.get("userdoc", {})
            devdoc = target_loaded.get("devdoc", {})
            natspec = Natspec(userdoc, devdoc)
            compilation_unit.natspec[contract_name] = natspec

            compilation_unit.bytecodes_init[contract_name] = target_loaded["evm"]["bytecode"][
                "object"
            ]
            compilation_unit.srcmaps_init[contract_name] = target_loaded["evm"]["bytecode"][
                "sourceMap"
            ].split(";")
            compilation_unit.bytecodes_runtime[contract_name] = target_loaded["evm"][
                "deployedBytecode"
            ]["object"]
            compilation_unit.srcmaps_runtime[contract_name] = target_loaded["evm"][
                "deployedBytecode"
            ]["sourceMap"].split(";")

        compilation_unit.compiler_version = CompilerVersion(
            compiler=compiler, version=version, optimized=optimized
        )

    @staticmethod
    def is_supported(target: str, **kwargs: str) -> bool:
        """
        Check if the target is a waffle project

        :param target:
        :return:
        """
        waffle_ignore = kwargs.get("waffle_ignore", False)
        if waffle_ignore:
            return False

        # Avoid conflicts with hardhat
        if os.path.isfile(os.path.join(target, "hardhat.config.js")) | os.path.isfile(
            os.path.join(target, "hardhat.config.ts")
        ):
            return False

        if os.path.isfile(os.path.join(target, "waffle.json")):
            return True

        if os.path.isfile(os.path.join(target, "package.json")):
            with open(os.path.join(target, "package.json"), encoding="utf8") as file_desc:
                package = json.load(file_desc)
            if "dependencies" in package:
                return "ethereum-waffle" in package["dependencies"]
            if "devDependencies" in package:
                return "ethereum-waffle" in package["devDependencies"]

        return False

    def is_dependency(self, path: str) -> bool:
        """
        Check if the path is a dependency

        :param path:
        :return:
        """
        if path in self._cached_dependencies:
            return self._cached_dependencies[path]
        ret = "node_modules" in Path(path).parts
        self._cached_dependencies[path] = ret
        return ret

    def _guessed_tests(self) -> List[str]:
        """
        Guess the potential unit tests commands

        :return:
        """
        return ["npx mocha"]


def _load_config(config_file: str) -> Dict:
    """
    Load the config file

    :param config_file:
    :return:
    """
    with open(config_file, "r") as file_desc:
        content = file_desc.read()

    if "module.exports" in content:
        raise InvalidCompilation("module.export to supported for waffle")
    return json.loads(content)


def _get_version(compiler: str, cwd: str, config: Optional[Dict] = None) -> str:
    version = ""
    if config is not None and "solcVersion" in config:
        version = re.findall(r"\d+\.\d+\.\d+", config["solcVersion"])[0]

    elif config is not None and compiler == "dockerized-solc":
        version = config["docker-tag"]

    elif compiler == "native":
        cmd = ["solc", "--version"]
        try:
            with subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd
            ) as process:
                stdout_bytes, _ = process.communicate()
                stdout_txt = stdout_bytes.decode()  # convert bytestrings to unicode strings
                stdout = stdout_txt.split("\n")
                for line in stdout:
                    if "Version" in line:
                        version = re.findall(r"\d+\.\d+\.\d+", line)[0]
        except OSError as error:
            # pylint: disable=raise-missing-from
            raise InvalidCompilation(error)

    elif compiler in ["solc-js"]:
        cmd = ["solcjs", "--version"]
        try:
            with subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd
            ) as process:
                stdout_bytes, _ = process.communicate()
                stdout_txt = stdout_bytes.decode()  # convert bytestrings to unicode strings
                version = re.findall(r"\d+\.\d+\.\d+", stdout_txt)[0]
        except OSError as error:
            # pylint: disable=raise-missing-from
            raise InvalidCompilation(error)

    else:
        raise InvalidCompilation(f"Solidity version not found {compiler}")

    return version


def _relative_to_short(relative: Path) -> Path:
    short = relative
    try:
        short = short.relative_to(Path("contracts"))
    except ValueError:
        try:
            short = short.relative_to("node_modules")
        except ValueError:
            pass
    return short
