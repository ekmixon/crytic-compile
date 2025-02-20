"""
Standard crytic-compile export
"""
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Tuple, Type

from crytic_compile.compilation_unit import CompilationUnit
from crytic_compile.compiler.compiler import CompilerVersion
from crytic_compile.platform import Type as PlatformType
from crytic_compile.platform.abstract_platform import AbstractPlatform
from crytic_compile.utils.naming import Filename

# Cycle dependency
from crytic_compile.utils.natspec import Natspec

if TYPE_CHECKING:
    from crytic_compile import CryticCompile


def export_to_standard(crytic_compile: "CryticCompile", **kwargs: str) -> List[str]:
    """
    Export the project to the standard crytic compile format
    :param crytic_compile:
    :param kwargs:
    :return:
    """
    # Obtain objects to represent each contract

    output = generate_standard_export(crytic_compile)

    export_dir = kwargs.get("export_dir", "crytic-export")
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)

    target = (
        "contracts"
        if os.path.isdir(crytic_compile.target)
        else Path(crytic_compile.target).parts[-1]
    )

    path = os.path.join(export_dir, f"{target}.json")
    with open(path, "w", encoding="utf8") as file_desc:
        json.dump(output, file_desc)

    return [path]


class Standard(AbstractPlatform):
    """
    Standard platform (crytic-compile specific)
    """

    NAME = "Standard"
    PROJECT_URL = "https://github.com/crytic/crytic-compile"
    TYPE = PlatformType.STANDARD

    HIDE = True

    def __init__(self, target: str, **kwargs: str):
        """
        Initializes an object which represents solc standard json

        :param target: A string path to a standard json
        """
        super().__init__(str(target), **kwargs)
        self._underlying_platform: Type[AbstractPlatform] = Standard
        self._unit_tests: List[str] = []

    def compile(self, crytic_compile: "CryticCompile", **_kwargs: str) -> None:
        """
        Compile the target (load file)

        :param crytic_compile:
        :param target:
        :param kwargs:
        :return:
        """
        # pylint: disable=import-outside-toplevel
        from crytic_compile.crytic_compile import get_platforms

        with open(self._target, encoding="utf8") as file_desc:
            loaded_json = json.load(file_desc)
        (underlying_type, unit_tests) = load_from_compile(crytic_compile, loaded_json)
        underlying_type = PlatformType(underlying_type)
        platforms: List[Type[AbstractPlatform]] = get_platforms()
        platform = next((p for p in platforms if p.TYPE == underlying_type), Standard)
        self._underlying_platform = platform
        self._unit_tests = unit_tests

    @staticmethod
    def is_supported(target: str, **kwargs: str) -> bool:
        """
        Check if the target is the standard crytic compile export

        :param target:
        :return:
        """
        standard_ignore = kwargs.get("standard_ignore", False)
        if standard_ignore:
            return False
        if not Path(target).parts:
            return False
        return Path(target).parts[-1].endswith("_export.json")

    def is_dependency(self, path: str) -> bool:
        """
        Always return False

        :param path:
        :return:
        """
        # handled by crytic_compile_dependencies
        return False

    def _guessed_tests(self) -> List[str]:
        return self._unit_tests

    @property
    def platform_name_used(self) -> str:
        return self._underlying_platform.NAME

    @property
    def platform_project_url_used(self) -> str:
        return self._underlying_platform.PROJECT_URL

    @property
    def platform_type_used(self) -> PlatformType:
        return self._underlying_platform.TYPE


def generate_standard_export(crytic_compile: "CryticCompile") -> Dict:
    """
    Export the standard crytic compile export

    :param crytic_compile:
    :return:
    """
    compilation_units = {}
    for key, compilation_unit in crytic_compile.compilation_units.items():
        contracts = dict()
        for contract_name in compilation_unit.contracts_names:
            filename = compilation_unit.filename_of_contract(contract_name)
            libraries = compilation_unit.libraries_names_and_patterns(contract_name)
            contracts[contract_name] = {
                "abi": compilation_unit.abi(contract_name),
                "bin": compilation_unit.bytecode_init(contract_name),
                "bin-runtime": compilation_unit.bytecode_runtime(contract_name),
                "srcmap": ";".join(compilation_unit.srcmap_init(contract_name)),
                "srcmap-runtime": ";".join(compilation_unit.srcmap_runtime(contract_name)),
                "filenames": {
                    "absolute": filename.absolute,
                    "used": filename.used,
                    "short": filename.short,
                    "relative": filename.relative,
                },
                "libraries": dict(libraries) if libraries else dict(),
                "is_dependency": crytic_compile.is_dependency(filename.absolute),
                "userdoc": compilation_unit.natspec[contract_name].userdoc.export(),
                "devdoc": compilation_unit.natspec[contract_name].devdoc.export(),
            }

        # Create our root object to contain the contracts and other information.

        compiler: Dict = dict()
        if compilation_unit.compiler_version:
            compiler = {
                "compiler": compilation_unit.compiler_version.compiler,
                "version": compilation_unit.compiler_version.version,
                "optimized": compilation_unit.compiler_version.optimized,
            }

        compilation_units[key] = {
            "compiler": compiler,
            "asts": compilation_unit.asts,
            "contracts": contracts,
        }

    output = {
        "compilation_units": compilation_units,
        "package": crytic_compile.package,
        "working_dir": str(crytic_compile.working_dir),
        "type": int(crytic_compile.platform.platform_type_used),
        "unit_tests": crytic_compile.platform.guessed_tests(),
    }
    return output


def _load_from_compile_legacy(crytic_compile: "CryticCompile", loaded_json: Dict) -> None:
    compilation_unit = CompilationUnit(crytic_compile, "legacy")
    compilation_unit.asts = loaded_json["asts"]
    compilation_unit.compiler_version = CompilerVersion(
        compiler=loaded_json["compiler"]["compiler"],
        version=loaded_json["compiler"]["version"],
        optimized=loaded_json["compiler"]["optimized"],
    )
    for contract_name, contract in loaded_json["contracts"].items():
        compilation_unit.contracts_names.add(contract_name)
        filename = Filename(
            absolute=contract["filenames"]["absolute"],
            relative=contract["filenames"]["relative"],
            short=contract["filenames"]["short"],
            used=contract["filenames"]["used"],
        )
        compilation_unit.contracts_filenames[contract_name] = filename

        compilation_unit.abis[contract_name] = contract["abi"]
        compilation_unit.bytecodes_init[contract_name] = contract["bin"]
        compilation_unit.bytecodes_runtime[contract_name] = contract["bin-runtime"]
        compilation_unit.srcmaps_init[contract_name] = contract["srcmap"].split(";")
        compilation_unit.srcmaps_runtime[contract_name] = contract["srcmap-runtime"].split(";")
        compilation_unit.libraries[contract_name] = contract["libraries"]

        userdoc = contract.get("userdoc", {})
        devdoc = contract.get("devdoc", {})
        compilation_unit.natspec[contract_name] = Natspec(userdoc, devdoc)

        if contract["is_dependency"]:
            compilation_unit.crytic_compile.dependencies.add(filename.absolute)
            compilation_unit.crytic_compile.dependencies.add(filename.relative)
            compilation_unit.crytic_compile.dependencies.add(filename.short)
            compilation_unit.crytic_compile.dependencies.add(filename.used)


def load_from_compile(crytic_compile: "CryticCompile", loaded_json: Dict) -> Tuple[int, List[str]]:
    """
    Load from json

    :param crytic_compile:
    :param loaded_json:
    :return:
    """
    crytic_compile.package_name = loaded_json.get("package", None)

    if "compilation_units" not in loaded_json:
        _load_from_compile_legacy(crytic_compile, loaded_json)

    else:
        for key, compilation_unit_json in loaded_json["compilation_units"].items():
            compilation_unit = CompilationUnit(crytic_compile, key)
            compilation_unit.compiler_version = CompilerVersion(
                compiler=compilation_unit_json["compiler"]["compiler"],
                version=compilation_unit_json["compiler"]["version"],
                optimized=compilation_unit_json["compiler"]["optimized"],
            )
            for contract_name, contract in compilation_unit_json["contracts"].items():
                compilation_unit.contracts_names.add(contract_name)
                filename = Filename(
                    absolute=contract["filenames"]["absolute"],
                    relative=contract["filenames"]["relative"],
                    short=contract["filenames"]["short"],
                    used=contract["filenames"]["used"],
                )
                compilation_unit.contracts_filenames[contract_name] = filename

                compilation_unit.abis[contract_name] = contract["abi"]
                compilation_unit.bytecodes_init[contract_name] = contract["bin"]
                compilation_unit.bytecodes_runtime[contract_name] = contract["bin-runtime"]
                compilation_unit.srcmaps_init[contract_name] = contract["srcmap"].split(";")
                compilation_unit.srcmaps_runtime[contract_name] = contract["srcmap-runtime"].split(
                    ";"
                )
                compilation_unit.libraries[contract_name] = contract["libraries"]

                userdoc = contract.get("userdoc", {})
                devdoc = contract.get("devdoc", {})
                compilation_unit.natspec[contract_name] = Natspec(userdoc, devdoc)

                if contract["is_dependency"]:
                    crytic_compile.dependencies.add(filename.absolute)
                    crytic_compile.dependencies.add(filename.relative)
                    crytic_compile.dependencies.add(filename.short)
                    crytic_compile.dependencies.add(filename.used)
            compilation_unit.asts = compilation_unit_json["asts"]

    # Set our filenames
    for compilation_unit in crytic_compile.compilation_units.values():
        crytic_compile.filenames |= set(compilation_unit.contracts_filenames.values())

    crytic_compile.working_dir = loaded_json["working_dir"]

    return loaded_json["type"], loaded_json.get("unit_tests", [])


def _relative_to_short(relative: Path) -> Path:
    """

    :param relative:
    :return:
    """
    return relative
