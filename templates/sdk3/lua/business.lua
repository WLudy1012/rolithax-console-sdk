return {
    initial = { lifecycle = "等待启动", last = "" },
    commands = { refresh = { text = "list" } },
    on_log = function(line, state)
        if string.sub(line, 1, 6) == "Done (" then
            return { lifecycle = "运行中", last = string.sub(line, 1, 512) }
        end
        return {}
    end
}
