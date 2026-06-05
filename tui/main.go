package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

const (
	roomPollInterval    = 3 * time.Second
	memoriaPollInterval = 5 * time.Second
	historyLimit        = 50
)

type (
	pollTickMsg    struct{}
	memoriaTickMsg struct{}
	errMsg         struct{ err error }
	chatSentMsg    struct{}
)

type consolidateMsg struct{}
type clearProposalsMsg struct{}

type model struct {
	width, height int
	ready         bool
	loading       bool

	chitchat *ChitchatClient
	memoria  *MemoriaClient

	chatViewport viewport.Model
	chatInput    textinput.Model
	rooms        []string
	activeRoom   int
	chatMessages []ChatMessage

	activeTab int
	tabs      []string

	agents []AgentInfo
	tasks  []TaskInfo
	memory *MemoryEntry
	recall        []RecallHit
	health        *HealthInfo
	memoriaConfig *MemoriaConfig

	recallInput textinput.Model
	recallQuery string

	memoryProject textinput.Model

	statusText string
	err        error

	spinner          spinner.Model
	currentThemeIdx  int
	styles           StyleBundle
}

func initialModel() model {
	ci := textinput.New()
	ci.Placeholder = "type a message..."
	ci.Width = 40

	ri := textinput.New()
	ri.Placeholder = "search query..."
	ri.Prompt = "recall> "
	ri.Width = 40

	mp := textinput.New()
	mp.Placeholder = "project name (default: cwd)..."
	mp.Prompt = "project> "
	mp.Width = 40

	s := spinner.New()
	s.Spinner = spinner.Dot

	m := model{
		chitchat:    NewChitchatClient(),
		memoria:     NewMemoriaClient(),
		rooms:       []string{"general"},
		activeRoom:  0,
		activeTab:   0,
		tabs:        []string{"Agents", "Tasks", "Memory", "Recall", "Settings"},
		chatInput:   ci,
		recallInput: ri,
		memoryProject: mp,
		spinner:     s,
		loading:     true,
		currentThemeIdx: 0,
	}

	m.applyTheme()
	return m
}

func (m *model) applyTheme() {
	m.styles = NewStyleBundle(themes[m.currentThemeIdx])
	m.spinner.Style = m.styles.Spinner
	m.chatInput.PromptStyle = m.styles.Prompt
	m.recallInput.PromptStyle = m.styles.Prompt
	m.memoryProject.PromptStyle = m.styles.Prompt
}

func (m model) Init() tea.Cmd {
	return tea.Batch(
		m.spinner.Tick,
		pollRooms(),
		pollMemoria(),
		initialLoad(m.chitchat, m.memoria),
		sendDefaultSize,
	)
}

func initialLoad(cc *ChitchatClient, mc *MemoriaClient) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()

		done := make(chan struct{})
		var health *HealthInfo
		var agents []AgentInfo
		var tasks []TaskInfo
		var cfg *MemoriaConfig

		go func(ctx context.Context) {
			defer close(done)
			select {
			case <-ctx.Done():
				return
			default:
			}
			cc.History("general", historyLimit)
			h, _ := mc.Health()
			a, _ := mc.Agents()
			t, _ := mc.Tasks("")
			c, _ := mc.GetConfig()
			health = h
			agents = a
			tasks = t
			cfg = c
		}(ctx)

		select {
		case <-done:
		case <-ctx.Done():
		}

		return initialDataMsg{
			health:  health,
			agents:  agents,
			tasks:   tasks,
			config:  cfg,
		}
	}
}

func sendDefaultSize() tea.Msg {
	time.Sleep(500 * time.Millisecond)
	return tea.WindowSizeMsg{Width: 80, Height: 24}
}

type initialDataMsg struct {
	health *HealthInfo
	agents []AgentInfo
	tasks  []TaskInfo
	config *MemoriaConfig
}

func pollRooms() tea.Cmd {
	return tea.Tick(roomPollInterval, func(t time.Time) tea.Msg {
		return pollTickMsg{}
	})
}

func pollMemoria() tea.Cmd {
	return tea.Tick(memoriaPollInterval, func(t time.Time) tea.Msg {
		return memoriaTickMsg{}
	})
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.ready = true
		m.layout()
		m.chatViewport.YPosition = 0
		m.chatViewport.SetContent(m.renderChatMessages())

	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+c", "q":
			return m, tea.Quit

		case "tab":
			if m.chatInput.Focused() {
				m.chatInput.Blur()
			} else {
				m.recallInput.Blur()
				m.memoryProject.Blur()
				m.chatInput.Focus()
			}
		}

		if !m.chatInput.Focused() && !m.recallInput.Focused() && !m.memoryProject.Focused() {
			switch msg.String() {
			case "left":
				if m.activeTab == 4 {
					m.currentThemeIdx = (m.currentThemeIdx - 1 + len(themes)) % len(themes)
					m.applyTheme()
					if m.ready {
						m.chatViewport.SetContent(m.renderChatMessages())
					}
				} else if m.activeRoom > 0 {
					m.activeRoom--
					m.loadChatHistory()
				}
			case "right":
				if m.activeTab == 4 {
					m.currentThemeIdx = (m.currentThemeIdx + 1) % len(themes)
					m.applyTheme()
					if m.ready {
						m.chatViewport.SetContent(m.renderChatMessages())
					}
				} else if m.activeRoom < len(m.rooms)-1 {
					m.activeRoom++
					m.loadChatHistory()
				}
			case "h":
				if m.activeTab > 0 {
					m.activeTab--
				}
			case "l":
				if m.activeTab < len(m.tabs)-1 {
					m.activeTab++
				}
			case "1", "2", "3", "4", "5":
				idx := int(msg.String()[0] - '1')
				if idx >= 0 && idx < len(m.tabs) {
					m.activeTab = idx
				}
			case "r":
				if m.activeTab == 3 {
					m.recallInput.Focus()
				}
			case "enter":
				if m.activeTab == 2 {
					m.memoryProject.Focus()
				}
			case "t":
				m.currentThemeIdx = (m.currentThemeIdx + 1) % len(themes)
				m.applyTheme()
				if m.ready {
					m.chatViewport.SetContent(m.renderChatMessages())
				}
			case "c":
				if m.activeTab == 4 {
					cmds = append(cmds, doConsolidate(m.memoria))
				}
			case "p":
				if m.activeTab == 4 {
					cmds = append(cmds, doClearProposals(m.memoria))
				}
			}
		}

		if m.chatInput.Focused() {
			if msg.String() == "enter" {
				text := m.chatInput.Value()
				if strings.TrimSpace(text) != "" {
					room := m.rooms[m.activeRoom]
					cmds = append(cmds, sendChat(m.chitchat, room, text))
					m.chatInput.SetValue("")
				}
			}
			var cmd tea.Cmd
			m.chatInput, cmd = m.chatInput.Update(msg)
			cmds = append(cmds, cmd)
		}

		if m.recallInput.Focused() {
			if msg.String() == "enter" {
				m.recallQuery = m.recallInput.Value()
				m.recallInput.SetValue("")
				m.recallInput.Blur()
				cmds = append(cmds, doRecall(m.memoria, m.recallQuery))
			}
			if msg.String() == "esc" {
				m.recallInput.Blur()
			}
			var cmd tea.Cmd
			m.recallInput, cmd = m.recallInput.Update(msg)
			cmds = append(cmds, cmd)
		}

		if m.memoryProject.Focused() {
			if msg.String() == "enter" {
				proj := m.memoryProject.Value()
				if proj == "" {
					proj = "memoria"
				}
				m.memoryProject.SetValue("")
				m.memoryProject.Blur()
				cmds = append(cmds, loadMemory(m.memoria, proj))
			}
			if msg.String() == "esc" {
				m.memoryProject.Blur()
			}
			var cmd tea.Cmd
			m.memoryProject, cmd = m.memoryProject.Update(msg)
			cmds = append(cmds, cmd)
		}

	case initialDataMsg:
		m.loading = false
		m.ready = true
		m.health = msg.health
		m.agents = msg.agents
		m.tasks = msg.tasks
		m.memoriaConfig = msg.config
		m.loadChatHistory()
		if msg.health != nil {
			m.statusText = fmt.Sprintf("memoria v%s | %d sessions | %d agents | %d tasks",
				msg.health.MemoriaVersion,
				msg.health.SessionsIndexed,
				len(msg.agents),
				len(msg.tasks),
			)
		}

	case pollTickMsg:
		cmds = append(cmds, m.refreshChat())
		cmds = append(cmds, pollRooms())

	case memoriaTickMsg:
		cmds = append(cmds, m.refreshMemoria())
		cmds = append(cmds, pollMemoria())

	case chatSentMsg:
		cmds = append(cmds, m.refreshChat())

	case recallResultsMsg:
		m.recall = msg.results
		m.statusText = fmt.Sprintf("recall: %d results", len(msg.results))

	case memoryLoadedMsg:
		m.memory = msg.mem
		m.statusText = fmt.Sprintf("memory: %s (%d entries)", msg.mem.Project, msg.mem.Count)

	case consolidateMsg:
		m.statusText = "consolidation triggered"
	case clearProposalsMsg:
		m.statusText = "proposals cleared"

	case errMsg:
		m.err = msg.err
		m.statusText = fmt.Sprintf("error: %v", msg.err)

	default:
		var cmd tea.Cmd
		m.spinner, cmd = m.spinner.Update(msg)
		cmds = append(cmds, cmd)
	}

	var cmd tea.Cmd
	m.chatViewport, cmd = m.chatViewport.Update(msg)
	cmds = append(cmds, cmd)

	return m, tea.Batch(cmds...)
}

func sendChat(cc *ChitchatClient, room, text string) tea.Cmd {
	return func() tea.Msg {
		if err := cc.Say(room, "you", text); err != nil {
			return errMsg{err}
		}
		time.Sleep(300 * time.Millisecond)
		return chatSentMsg{}
	}
}

func doRecall(mc *MemoriaClient, query string) tea.Cmd {
	return func() tea.Msg {
		results, err := mc.Recall(query, 10)
		if err != nil {
			return errMsg{err}
		}
		return recallResultsMsg{results}
	}
}

type recallResultsMsg struct {
	results []RecallHit
}

func doConsolidate(mc *MemoriaClient) tea.Cmd {
	return func() tea.Msg {
		if err := mc.TriggerConsolidation(); err != nil {
			return errMsg{err}
		}
		return consolidateMsg{}
	}
}

func doClearProposals(mc *MemoriaClient) tea.Cmd {
	return func() tea.Msg {
		if err := mc.ClearProposals(); err != nil {
			return errMsg{err}
		}
		return clearProposalsMsg{}
	}
}

func loadMemory(mc *MemoriaClient, project string) tea.Cmd {
	return func() tea.Msg {
		mem, err := mc.Memory(project)
		if err != nil {
			return errMsg{err}
		}
		return memoryLoadedMsg{mem}
	}
}

type memoryLoadedMsg struct {
	mem *MemoryEntry
}

func (m *model) refreshChat() tea.Cmd {
	room := m.rooms[m.activeRoom]
	_, err := m.chitchat.History(room, historyLimit)
	if err != nil {
		return func() tea.Msg { return errMsg{err} }
	}
	m.chatMessages = m.chitchat.GetMessages(room)
	m.chatViewport.SetContent(m.renderChatMessages())
	m.chatViewport.GotoBottom()
	return nil
}

func (m *model) refreshMemoria() tea.Cmd {
	health, err := m.memoria.Health()
	if err != nil {
		return func() tea.Msg { return errMsg{err} }
	}
	m.health = health
	cfg, _ := m.memoria.GetConfig()
	m.memoriaConfig = cfg
	agents, _ := m.memoria.Agents()
	m.agents = agents
	tasks, _ := m.memoria.Tasks("")
	m.tasks = tasks
	if health != nil {
		m.statusText = fmt.Sprintf("memoria v%s | %d sessions | %d agents | %d tasks",
			health.MemoriaVersion,
			health.SessionsIndexed,
			len(agents),
			len(tasks),
		)
	}
	return nil
}

func (m *model) loadChatHistory() {
	room := m.rooms[m.activeRoom]
	msgs, err := m.chitchat.History(room, historyLimit)
	if err != nil {
		m.err = err
		return
	}
	m.chatMessages = msgs
	if m.ready {
		m.chatViewport.SetContent(m.renderChatMessages())
		m.chatViewport.GotoBottom()
	}
}

func (m *model) layout() {
	chatWidth := m.width * 45 / 100
	if chatWidth < 40 {
		chatWidth = 40
	}
	dashWidth := m.width - chatWidth - 6

	m.styles.LeftPane = m.styles.LeftPane.Width(chatWidth - 4).Height(m.height - 6)
	m.styles.RightPane = m.styles.RightPane.Width(dashWidth - 4).Height(m.height - 6)

	m.chatViewport.Width = chatWidth - 8
	m.chatViewport.Height = m.height - 12
	m.chatViewport.YPosition = 0

	m.chatInput.Width = chatWidth - 12
	m.recallInput.Width = dashWidth - 12
	m.memoryProject.Width = dashWidth - 12
}

func (m model) View() string {
	if !m.ready {
		return "\n  " + m.spinner.View() + " Loading..."
	}

	if m.loading {
		return m.spinner.View() + " Connecting to memoria & chitchat..."
	}

	chatPane := m.renderChatPane()
	dashPane := m.renderDashboardPane()
	statusBar := m.renderStatusBar()

	panes := lipgloss.JoinHorizontal(lipgloss.Top, chatPane, dashPane)
	return lipgloss.JoinVertical(lipgloss.Left, panes, statusBar)
}

func (m model) renderChatPane() string {
	roomTabs := m.renderRoomTabs()
	viewport := m.styles.LeftPane.Render(roomTabs + "\n" + m.chatViewport.View())
	input := m.styles.Input.Render(m.chatInput.View())
	return lipgloss.JoinVertical(lipgloss.Left, viewport, input)
}

func (m model) renderRoomTabs() string {
	var tabs []string
	for i, room := range m.rooms {
		if i == m.activeRoom {
			tabs = append(tabs, m.styles.ActiveRoomTab.Render(room))
		} else {
			tabs = append(tabs, m.styles.RoomTab.Render(room))
		}
	}
	return lipgloss.JoinHorizontal(lipgloss.Top, tabs...)
}

func (m model) renderDashboardPane() string {
	tabBar := m.renderTabBar()
	content := m.styles.RightPane.Render(tabBar + "\n\n" + m.renderTabContent())
	return content
}

func (m model) renderTabBar() string {
	var tabs []string
	for i, tab := range m.tabs {
		if i == m.activeTab {
			tabs = append(tabs, m.styles.ActiveTab.Render(tab))
		} else {
			tabs = append(tabs, m.styles.Tab.Render(tab))
		}
	}
	return lipgloss.JoinHorizontal(lipgloss.Top, tabs...)
}

func (m model) renderTabContent() string {
	switch m.activeTab {
	case 0:
		return m.renderAgents()
	case 1:
		return m.renderTasks()
	case 2:
		return m.renderMemory()
	case 3:
		return m.renderRecall()
	case 4:
		return m.renderSettings()
	default:
		return "unknown tab"
	}
}

func (m model) renderAgents() string {
	if len(m.agents) == 0 {
		return m.styles.Dim.Render("No active agents.")
	}
	var lines []string
	for _, a := range m.agents {
		dot := m.statusDotForAgent(a.Status)
		id := a.ID
		if len(id) > 16 {
			id = id[:16] + "..."
		}
		task := a.Task
		if len(task) > 40 {
			task = task[:40] + "..."
		}
		started := time.Unix(int64(a.StartedAt), 0).Format("15:04")
		line := fmt.Sprintf(" %s %s  %s  [%s]", dot, m.styles.SettingsKey.Render(id), task, started)
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func (m model) statusDotForAgent(status string) string {
	switch status {
	case "active":
		return m.styles.DotGreen
	case "idle", "pending":
		return m.styles.DotYellow
	case "error", "failed", "stale":
		return m.styles.DotRed
	default:
		return m.styles.DotDim
	}
}

func (m model) renderTasks() string {
	if len(m.tasks) == 0 {
		return m.styles.Dim.Render("No tasks.")
	}
	var lines []string
	for _, t := range m.tasks {
		dot := m.statusDotForTask(t.Status)
		id := t.ID
		if len(id) > 16 {
			id = id[:16] + "..."
		}
		title := t.Title
		if len(title) > 35 {
			title = title[:35] + "..."
		}
		assigned := t.AssignedTo
		if assigned == "" {
			assigned = "unassigned"
		}
		line := fmt.Sprintf(" %s %s  %s  [%s]", dot, m.styles.SettingsKey.Render(id), title, assigned)
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func (m model) statusDotForTask(status string) string {
	switch status {
	case "done", "completed":
		return m.styles.DotGreen
	case "in_progress", "assigned":
		return m.styles.DotYellow
	case "failed", "blocked":
		return m.styles.DotRed
	default:
		return m.styles.DotDim
	}
}

func (m model) renderMemory() string {
	if m.memoryProject.Focused() {
		return m.memoryProject.View()
	}
	if m.memory == nil {
		return m.styles.Dim.Render("Press Enter to load a project's memory, or type project name and Enter.")
	}
	var lines []string
	lines = append(lines, m.styles.Info.Render(fmt.Sprintf("Project: %s (%d entries)", m.memory.Project, m.memory.Count)))
	if len(m.memory.Entries) == 0 {
		lines = append(lines, m.styles.Dim.Render("No memory entries."))
	} else {
		for i, e := range m.memory.Entries {
			if i >= 20 {
				lines = append(lines, m.styles.Dim.Render(fmt.Sprintf("... and %d more", len(m.memory.Entries)-20)))
				break
			}
			text := e
			if len(text) > 60 {
				text = text[:60] + "..."
			}
			lines = append(lines, fmt.Sprintf("  %s %s", m.styles.DotDim, text))
		}
	}
	lines = append(lines, "", m.styles.Dim.Render("Enter: load another project"))
	return strings.Join(lines, "\n")
}

func (m model) renderRecall() string {
	if m.recallInput.Focused() {
		return m.recallInput.View()
	}
	if m.recallQuery == "" {
		return m.styles.Dim.Render("Press 'r' to search past sessions.")
	}
	if len(m.recall) == 0 {
		return fmt.Sprintf("No results for: %s", m.recallQuery)
	}
	var lines []string
	lines = append(lines, m.styles.Info.Render(fmt.Sprintf("Recall: %s (%d results)", m.recallQuery, len(m.recall))))
	for _, r := range m.recall {
		id := r.ID
		if len(id) > 12 {
			id = id[:12]
		}
		title := r.Title
		if len(title) > 40 {
			title = title[:40] + "..."
		}
		summary := r.Summary
		if len(summary) > 60 {
			summary = summary[:60] + "..."
		}
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.SettingsKey.Render("["+id+"]"), title))
		lines = append(lines, fmt.Sprintf("       %s", m.styles.Dim.Render(summary)))
	}
	lines = append(lines, "", m.styles.Dim.Render("Press 'r' to search again"))
	return strings.Join(lines, "\n")
}

func (m model) renderChatMessages() string {
	if len(m.chatMessages) == 0 {
		return m.styles.Dim.Render("No messages in this room yet.")
	}
	var lines []string
	for _, msg := range m.chatMessages {
		ts := ""
		if len(msg.TS) >= 16 {
			ts = msg.TS[11:16]
		} else if len(msg.TS) >= 5 {
			ts = msg.TS[:5]
		}
		from := msg.From
		if from == "" {
			from = "?"
		}

		tsStr := m.styles.Timestamp.Render("[" + ts + "]")

		var senderStr string
		if from == "you" || from == "user" {
			senderStr = m.styles.UserMsg.Render(from + ":")
		} else if msg.Type == "system" {
			senderStr = m.styles.SystemMsg.Render(from + ":")
		} else {
			senderStr = m.styles.AgentMsg.Render(from + ":")
		}

		lines = append(lines, fmt.Sprintf(" %s %s %s",
			tsStr, senderStr, msg.Text))
	}
	return strings.Join(lines, "\n")
}

func (m model) renderStatusBar() string {
	healthDot := m.styles.DotRed
	if m.health != nil {
		healthDot = m.styles.DotGreen
	}
	left := fmt.Sprintf(" %s memoria  %s chitchat  %s %s ",
		healthDot, m.styles.DotGreen, m.styles.DotDim, m.rooms[m.activeRoom])
	right := " [h/l] tabs  [←/→] rooms  [tab] input  [t] theme  [q] quit "
	spacing := m.width - lipgloss.Width(left) - lipgloss.Width(right)
	if spacing < 1 {
		spacing = 1
	}
	bar := left + strings.Repeat(" ", spacing) + right
	return m.styles.StatusBar.Render(bar)
}

func main() {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "--sshd" {
		log.Println("starting memoria TUI SSH server...")
		if err := startSSHServer(); err != nil {
			log.Fatalf("SSH server: %v", err)
		}
		return
	}
	if len(args) > 0 && args[0] == "--help" {
		fmt.Println("memoria-tui — AgentOS terminal dashboard")
		fmt.Println()
		fmt.Println("Usage:")
		fmt.Println("  tui            Run as local TUI")
		fmt.Println("  tui --sshd     Run as SSH server on port 23234")
		fmt.Println()
		fmt.Println("Controls:")
		fmt.Println("  ← →            Switch chat rooms / cycle themes on Settings tab")
		fmt.Println("  h l             Switch dashboard tabs")
		fmt.Println("  1-5             Jump to dashboard tab")
		fmt.Println("  tab             Toggle chat input focus")
		fmt.Println("  t               Next theme")
		fmt.Println("  c               Force consolidate (Settings tab)")
		fmt.Println("  p               Clear proposals (Settings tab)")
		fmt.Println("  r               Focus recall search")
		fmt.Println("  enter           Send message / submit")
		fmt.Println("  q / ctrl+c      Quit")
		return
	}
	p := tea.NewProgram(initialModel(), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		panic(err)
	}
}
