{
  description = "formula-screening dev shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.uv
          pkgs.git-lfs
        ];

        # pip/uvでインストールしたnumpy等のC拡張に必要なシステムライブラリを提供
        env.LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
          pkgs.stdenv.cc.cc.lib # libstdc++.so.6
          pkgs.zlib # libz.so.1
        ];

        shellHook = ''
          # uvはPATH上のPythonではなく.venv内のPythonを使う
          export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
        '';
      };
    };
}
