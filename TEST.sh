#!/usr/bin/env bb

(require '[babashka.fs :as fs]
         '[babashka.process :as p])

(let [script-dir (str (fs/canonicalize (fs/parent *file*)))
      existing-py (System/getenv "PYTHONPATH")
      local-src (str script-dir "/src")
      py-path (if (seq existing-py) (str local-src ":" existing-py) local-src)
      cmd (into ["uv" "run" "pytest"] *command-line-args*)
      proc (p/process cmd {:inherit true
                           :extra-env {"PYTHONPATH" py-path}})
      exit-code (.waitFor (:proc proc))]
  (System/exit exit-code))
