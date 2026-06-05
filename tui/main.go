package main

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

const (
	roomPollInterval   = 3 * time.Second
	memoriaPollInterval = 5 * time.Second
	historyLimit       = 50
)

type (
	pollTickMsg      struct{} // ticker for chitchat polling
	memoriaTickMsg   struct{} // ticker for memoria polling
	errMsg           struct{ err error }
	chatSentMsg      struct{}
)

type model struct {
	width, height int
	ready         bool
	loading       bool

	// Clients
	chitchat *ChitchatClient
	memoria  *MemoriaClient

	// Chat pane
	chatViewport viewport.Model
	chatInput    textinput.Model
	rooms        []string
	activeRoom   int
	chatMessages []ChatMessage

	// Dashboard pane tabs
	activeTab int
	tabs      []string

	// Dashboard data
	agents []AgentInfo
	tasks  []TaskInfo
	memory *MemoryEntry
	recall []RecallHit
	health *HealthInfo

	// Recall input
	recallInput textinput.Model
	recallQuery string

	// Memory input
	memoryProject textinput.Model

	// Status
	statusText string
	err        error

	spinner spinner.Model
}

func initialModel() model {
	ci := textinput.New()
	ci.Placeholder = "type a message..."
	ci.PromptStyle = promptStyle
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
	s.Style = spinnerStyle
	s.Spinner = spinner.Dot

	return model{
		chitchat:    NewChitchatClient(),
		memoria:     NewMemoriaClient(),
		rooms:       []string{"general"},
		activeRoom:  0,
		activeTab:   0,
		tabs:        []string{"Agents", "Tasks", "Memory", "Recall"},
		chatInput:   ci,
		recallInput: ri,
		memoryProject: mp,
		spinner:     s,
		loading:     true,
	}
}

func (m model) Init() tea.Cmd {
	return tea.Batch(
		m.spinner.Tick,
		pollRooms(),
		pollMemoria(),
		initialLoad(m.chitchat, m.memoria),
	)
}

func initialLoad(cc *ChitchatClient, mc *MemoriaClient) tea.Cmd {
	return func() tea.Msg {
		cc.History("general", historyLimit)
		health, _ := mc.Health()
		agents, _ := mc.Agents()
		tasks, _ := mc.Tasks("")
		return initialDataMsg{
			health: health,
			agents: agents,
			tasks:  tasks,
		}
	}
}

type initialDataMsg struct {
	health *HealthInfo
	agents []AgentInfo
	tasks  []TaskInfo
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
				m.chatInput.Focus()
			}
		}

		// Global keybindings when chat input is NOT focused
		if !m.chatInput.Focused() && !m.recallInput.Focused() && !m.memoryProject.Focused() {
			switch msg.String() {
			case "left":
				if m.activeRoom > 0 {
					m.activeRoom--
					m.loadChatHistory()
				}
			case "right":
				if m.activeRoom < len(m.rooms)-1 {
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
			case "1", "2", "3", "4":
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
			}
		}

		// Handle focused inputs
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
		m.health = msg.health
		m.agents = msg.agents
		m.tasks = msg.tasks
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
		cc.History(room, historyLimit)
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

	// Update pane styles
	leftStyle = leftStyle.Width(chatWidth - 4).Height(m.height - 6)
	rightStyle = rightStyle.Width(dashWidth - 4).Height(m.height - 6)

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
	viewport := leftStyle.Render(roomTabs + "\n" + m.chatViewport.View())
	input := inputStyle.Render(m.chatInput.View())
	return lipgloss.JoinVertical(lipgloss.Left, viewport, input)
}

func (m model) renderRoomTabs() string {
	var tabs []string
	for i, room := range m.rooms {
		if i == m.activeRoom {
			tabs = append(tabs, activeRoomTabStyle.Render(room))
		} else {
			tabs = append(tabs, roomTabStyle.Render(room))
		}
	}
	return lipgloss.JoinHorizontal(lipgloss.Top, tabs...)
}

func (m model) renderDashboardPane() string {
	tabBar := m.renderTabBar()
	content := rightStyle.Render(tabBar + "\n\n" + m.renderTabContent())
	return content
}

func (m model) renderTabBar() string {
	var tabs []string
	for i, tab := range m.tabs {
		if i == m.activeTab {
			tabs = append(tabs, activeTabStyle.Render(tab))
		} else {
			tabs = append(tabs, tabStyle.Render(tab))
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
	default:
		return "unknown tab"
	}
}

func (m model) renderAgents() string {
	if len(m.agents) == 0 {
		return dimStyle.Render("No active agents.")
	}
	var lines []string
	for _, a := range m.agents {
		id := a.ID
		if len(id) > 16 {
			id = id[:16] + "..."
		}
		status := agentStatusStyle.Copy().Render(a.Status)
		task := a.Task
		if len(task) > 40 {
			task = task[:40] + "..."
		}
		started := time.Unix(int64(a.StartedAt), 0).Format("15:04")
		line := fmt.Sprintf("%s %s  %s  [%s]", status, id, task, started)
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func (m model) renderTasks() string {
	if len(m.tasks) == 0 {
		return dimStyle.Render("No tasks.")
	}
	var lines []string
	for _, t := range m.tasks {
		id := t.ID
		if len(id) > 16 {
			id = id[:16] + "..."
		}
		status := taskStatusStyle.Copy().Render(t.Status)
		title := t.Title
		if len(title) > 35 {
			title = title[:35] + "..."
		}
		assigned := t.AssignedTo
		if assigned == "" {
			assigned = "unassigned"
		}
		line := fmt.Sprintf("%s %s  %s  [%s]", status, id, title, assigned)
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func (m model) renderMemory() string {
	if m.memoryProject.Focused() {
		return m.memoryProject.View()
	}
	if m.memory == nil {
		return dimStyle.Render("Press Enter to load a project's memory, or type project name and Enter.")
	}
	var lines []string
	lines = append(lines, infoStyle.Render(fmt.Sprintf("Project: %s (%d entries)", m.memory.Project, m.memory.Count)))
	if len(m.memory.Entries) == 0 {
		lines = append(lines, dimStyle.Render("No memory entries."))
	} else {
		for i, e := range m.memory.Entries {
			if i >= 20 {
				lines = append(lines, dimStyle.Render(fmt.Sprintf("... and %d more", len(m.memory.Entries)-20)))
				break
			}
			text := e
			if len(text) > 60 {
				text = text[:60] + "..."
			}
			lines = append(lines, fmt.Sprintf("  %d. %s", i+1, text))
		}
	}
	lines = append(lines, "", dimStyle.Render("Enter: load another project"))
	return strings.Join(lines, "\n")
}

func (m model) renderRecall() string {
	if m.recallInput.Focused() {
		return m.recallInput.View()
	}
	if m.recallQuery == "" {
		return dimStyle.Render("Press 'r' to search past sessions.")
	}
	if len(m.recall) == 0 {
		return fmt.Sprintf("No results for: %s", m.recallQuery)
	}
	var lines []string
	lines = append(lines, infoStyle.Render(fmt.Sprintf("Recall: %s (%d results)", m.recallQuery, len(m.recall))))
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
		lines = append(lines, fmt.Sprintf("  [%s] %s", id, title))
		lines = append(lines, fmt.Sprintf("       %s", summary))
	}
	lines = append(lines, "", dimStyle.Render("Press 'r' to search again"))
	return strings.Join(lines, "\n")
}

func (m model) renderChatMessages() string {
	if len(m.chatMessages) == 0 {
		return dimStyle.Render("No messages in this room yet.")
	}
	var lines []string
	for _, msg := range m.chatMessages {
		lines = append(lines, msg.String())
	}
	return strings.Join(lines, "\n")
}

func (m model) renderStatusBar() string {
	health := "?"
	if m.health != nil {
		health = "ok"
	}
	left := fmt.Sprintf(" memoria: %s | chitchat: ok | room: %s ", health, m.rooms[m.activeRoom])
	right := " [h/l] tabs  [←/→] rooms  [tab] input  [q] quit "
	spacing := m.width - lipgloss.Width(left) - lipgloss.Width(right)
	if spacing < 1 {
		spacing = 1
	}
	bar := left + strings.Repeat(" ", spacing) + right
	return statusStyle.Render(bar)
}

func main() {
	p := tea.NewProgram(initialModel(), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		panic(err)
	}
}
