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

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

const (
	roomPollInterval    = 3 * time.Second
	memoriaPollInterval = 5 * time.Second
	historyLimit        = 50

	tabOverview = 0
	tabAgents   = 1
	tabTasks    = 2
	tabMemory   = 3
	tabRecall   = 4
	tabSettings = 5
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

	focusMode       int
	settingsFocusIdx int
	agentFocusIdx    int
	taskFocusIdx     int

	hideAgentOs bool
	hideSystem  bool

	detailView  bool
	detailIdx   int

	chatPaneW int
	dashPaneX int
	tabWidths []int
}

const (
	focusChat     = 0
	focusDashboard = 1
	focusInput    = 2
)

func initialModel() model {
	ci := textinput.New()
	ci.Placeholder = "type a message..."
	ci.Width = 60

	ri := textinput.New()
	ri.Placeholder = "search query..."
	ri.Prompt = "recall> "
	ri.Width = 60

	mp := textinput.New()
	mp.Placeholder = "project name (default: cwd)..."
	mp.Prompt = "project> "
	mp.Width = 60

	s := spinner.New()
	s.Spinner = spinner.Dot

	m := model{
		chitchat:    NewChitchatClient(),
		memoria:     NewMemoriaClient(),
		rooms:       []string{"general"},
		activeRoom:  0,
		activeTab:   tabOverview,
		tabs:        []string{"Overview", "Agents", "Tasks", "Memory", "Recall", "Settings"},
		chatInput:   ci,
		recallInput: ri,
		memoryProject: mp,
		spinner:     s,
		loading:     true,
		currentThemeIdx: 0,
		focusMode:   0,
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
			health: health,
			agents: agents,
			tasks:  tasks,
			config: cfg,
		}
	}
}

func sendDefaultSize() tea.Msg {
	time.Sleep(500 * time.Millisecond)
	return tea.WindowSizeMsg{Width: 120, Height: 36}
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
		case "ctrl+c":
			return m, tea.Quit

		case "1", "2", "3", "4", "5", "6":
			idx := int(msg.String()[0] - '1')
			if idx >= 0 && idx < len(m.tabs) {
				m.activeTab = idx
			}

		case "tab":
			if m.chatInput.Focused() {
				m.chatInput.Blur()
				m.focusMode = focusDashboard
			} else if m.recallInput.Focused() {
				m.recallInput.Blur()
				m.focusMode = focusDashboard
			} else if m.memoryProject.Focused() {
				m.memoryProject.Blur()
				m.focusMode = focusDashboard
			} else if m.focusMode == focusDashboard {
				m.focusMode = focusInput
				m.chatInput.Focus()
			} else {
				m.focusMode = focusChat
			}
		case "esc":
			if m.recallInput.Focused() {
				m.recallInput.Blur()
				m.focusMode = focusDashboard
			} else if m.memoryProject.Focused() {
				m.memoryProject.Blur()
				m.focusMode = focusDashboard
			} else if m.chatInput.Focused() {
				m.chatInput.Blur()
				m.focusMode = focusChat
			} else {
				m.focusMode = focusChat
			}
		}

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
			case "up", "k":
				switch m.focusMode {
				case focusChat, focusInput:
					m.chatViewport.LineUp(3)
				case focusDashboard:
					switch m.activeTab {
					case tabSettings:
						if m.settingsFocusIdx > 0 {
							m.settingsFocusIdx--
						}
					case tabAgents:
						if m.agentFocusIdx > 0 {
							m.agentFocusIdx--
						}
					case tabTasks:
						if m.taskFocusIdx > 0 {
							m.taskFocusIdx--
						}
					default:
						if m.activeTab > 0 {
							m.activeTab--
						}
					}
				}
			case "down", "j":
				switch m.focusMode {
				case focusChat, focusInput:
					m.chatViewport.LineDown(3)
				case focusDashboard:
					switch m.activeTab {
					case tabSettings:
						if m.settingsFocusIdx < settingsActionCount-1 {
							m.settingsFocusIdx++
						}
					case tabAgents:
						if m.agentFocusIdx < len(m.agents)-1 {
							m.agentFocusIdx++
						}
					case tabTasks:
						if m.taskFocusIdx < len(m.tasks)-1 {
							m.taskFocusIdx++
						}
					default:
						if m.activeTab < len(m.tabs)-1 {
							m.activeTab++
						}
					}
				}
			case "[":
				if m.activeRoom > 0 {
					m.activeRoom--
					m.loadChatHistory()
				}
			case "]":
				if m.activeRoom < len(m.rooms)-1 {
					m.activeRoom++
					m.loadChatHistory()
				}
			case "f":
				m.hideAgentOs = !m.hideAgentOs
				m.chatViewport.SetContent(m.renderChatMessages())
			case "F":
				m.hideSystem = !m.hideSystem
				m.chatViewport.SetContent(m.renderChatMessages())
			case "enter":
				switch m.focusMode {
				case focusDashboard:
					switch m.activeTab {
					case tabSettings:
						switch m.settingsFocusIdx {
						case 0:
							cmds = append(cmds, doConsolidate(m.memoria))
						case 1:
							cmds = append(cmds, doClearProposals(m.memoria))
						case 2:
							m.currentThemeIdx = (m.currentThemeIdx + 1) % len(themes)
							m.applyTheme()
							if m.ready {
								m.chatViewport.SetContent(m.renderChatMessages())
							}
						}
					case tabAgents:
						if m.agentFocusIdx >= 0 && m.agentFocusIdx < len(m.agents) {
							m.detailView = !m.detailView
							m.detailIdx = m.agentFocusIdx
						}
					case tabTasks:
						if m.taskFocusIdx >= 0 && m.taskFocusIdx < len(m.tasks) {
							m.detailView = !m.detailView
							m.detailIdx = m.taskFocusIdx
						}
					}
				default:
					m.chatInput.Focus()
					m.focusMode = focusInput
				}
			case "d":
				if m.detailView {
					m.detailView = false
				}
			case "i":
				m.chatInput.Focus()
				m.focusMode = focusInput
			case "r":
				if m.activeTab == tabRecall {
					m.recallInput.Focus()
					m.focusMode = focusInput
				}
			case "t":
				m.currentThemeIdx = (m.currentThemeIdx + 1) % len(themes)
				m.applyTheme()
				if m.ready {
					m.chatViewport.SetContent(m.renderChatMessages())
				}
			case "c":
				if m.activeTab == tabSettings {
					cmds = append(cmds, doConsolidate(m.memoria))
				}
			case "p":
				if m.activeTab == tabSettings {
					cmds = append(cmds, doClearProposals(m.memoria))
				}
			case "q":
				return m, tea.Quit
			case "?":
				if m.statusText == "" {
					m.statusText = "[h/l] tabs  [↑↓/j/k] nav  [Enter/i] chat  [d] close  [t] theme  [q] quit"
				} else {
					m.statusText = ""
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
				m.focusMode = focusDashboard
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
				m.focusMode = focusDashboard
			}
			var cmd tea.Cmd
			m.memoryProject, cmd = m.memoryProject.Update(msg)
			cmds = append(cmds, cmd)
		}

	case tea.MouseMsg:
		m.handleMouse(msg)

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

func (m *model) handleMouse(msg tea.MouseMsg) {
	if msg.Action == tea.MouseActionMotion {
		return
	}

	switch msg.Button {
	case tea.MouseButtonWheelUp:
		if msg.X < m.chatPaneW {
			m.chatViewport.LineUp(3)
		}
		return
	case tea.MouseButtonWheelDown:
		if msg.X < m.chatPaneW {
			m.chatViewport.LineDown(3)
		}
		return
	}

	if msg.Action != tea.MouseActionRelease {
		return
	}

	relX := msg.X

	if relX >= m.dashPaneX {
		relY := msg.Y
		if relY <= 1 {
			xAccum := m.dashPaneX + 2
			for i, tw := range m.tabWidths {
				if relX < xAccum+tw {
					m.activeTab = i
					return
				}
				xAccum += tw
			}
			return
		}

		contentStartY := 3
		contentY := relY - contentStartY
		if contentY < 0 {
			return
		}

		switch m.activeTab {
		case tabAgents:
			if m.detailView && m.detailIdx >= 0 && m.detailIdx < len(m.agents) {
				return
			}
			if contentY < len(m.agents) {
				m.agentFocusIdx = contentY
				m.focusMode = focusDashboard
			}
		case tabTasks:
			if m.detailView && m.detailIdx >= 0 && m.detailIdx < len(m.tasks) {
				return
			}
			if contentY < len(m.tasks) {
				m.taskFocusIdx = contentY
				m.focusMode = focusDashboard
			}
		case tabSettings:
			if contentY < settingsActionCount {
				m.settingsFocusIdx = contentY
				m.focusMode = focusDashboard
			}
		}
	} else if relX < m.chatPaneW {
		if msg.Y <= 0 && len(m.rooms) > 0 {
			roomAccum := 0
			for i, room := range m.rooms {
				rw := lipgloss.Width(m.styles.RoomTab.Render(room))
				if i == m.activeRoom {
					rw = lipgloss.Width(m.styles.ActiveRoomTab.Render(room))
				}
				if relX < roomAccum+rw {
					m.activeRoom = i
					m.loadChatHistory()
					return
				}
				roomAccum += rw
			}
		}
	}
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
	gap := 2
	w := m.width
	h := m.height

	var chatWidth int
	switch {
	case w >= 160:
		chatWidth = w * 50 / 100
	case w >= 100:
		chatWidth = w * 45 / 100
	default:
		chatWidth = w * 55 / 100
	}
	minChatWidth := max(30, w/4)
	if chatWidth < minChatWidth {
		chatWidth = minChatWidth
	}
	maxChatWidth := w - gap - 20
	if chatWidth > maxChatWidth {
		chatWidth = maxChatWidth
	}

	dashWidth := w - chatWidth - gap
	if dashWidth < 20 {
		dashWidth = 20
		chatWidth = w - dashWidth - gap
	}

	statusBarLines := 1
	inputBoxLines := 3
	borderLines := 2

	paneHeight := h - statusBarLines - inputBoxLines - borderLines
	if paneHeight < 6 {
		paneHeight = 6
	}

	m.chatPaneW = chatWidth
	m.dashPaneX = chatWidth + gap

	m.styles.LeftPane = m.styles.LeftPane.Width(chatWidth - 4).Height(paneHeight)
	m.styles.RightPane = m.styles.RightPane.Width(dashWidth - 4).Height(paneHeight)

	vpHeight := paneHeight - 2
	if vpHeight < 6 {
		vpHeight = 6
	}
	m.chatViewport.Width = chatWidth - 8
	if m.chatViewport.Width < 20 {
		m.chatViewport.Width = 20
	}
	m.chatViewport.Height = vpHeight
	m.chatViewport.YPosition = 0

	inputPad := 12
	m.chatInput.Width = max(20, chatWidth-inputPad)
	m.recallInput.Width = max(20, dashWidth-inputPad)
	m.memoryProject.Width = max(20, dashWidth-inputPad)
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

	panes := lipgloss.JoinHorizontal(lipgloss.Top, chatPane, "  ", dashPane)
	return lipgloss.JoinVertical(lipgloss.Left, panes, statusBar)
}

func (m model) renderChatPane() string {
	roomTabs := m.renderRoomTabs()
	filterBar := m.renderFilterBar()
	viewport := m.styles.LeftPane.Render(roomTabs + "\n" + filterBar + "\n" + m.chatViewport.View())
	input := m.styles.Input.Render(m.chatInput.View())
	footer := m.renderHelpFooter()
	return lipgloss.JoinVertical(lipgloss.Left, viewport, input, footer)
}

func (m model) renderFilterBar() string {
	agentOsTag := m.styles.Dim.Render("[x] agent-os")
	if m.hideAgentOs {
		agentOsTag = m.styles.FocusStyle.Render("[ ] agent-os")
	}
	systemTag := m.styles.Dim.Render("[x] system")
	if m.hideSystem {
		systemTag = m.styles.FocusStyle.Render("[ ] system")
	}
	return "  " + agentOsTag + "  " + systemTag + "  " + m.styles.Dim.Render("[f] toggle [F] system")
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
	content := m.styles.RightPane.Render(tabBar + "\n" + m.renderTabContent())
	footer := m.renderHelpFooter()
	return lipgloss.JoinVertical(lipgloss.Left, content, footer)
}

func (m model) renderTabBar() string {
	var tabs []string
	m.tabWidths = nil
	for i, tab := range m.tabs {
		var rendered string
		if i == m.activeTab {
			if m.focusMode == focusDashboard {
				rendered = m.styles.FocusStyle.Render(" " + tab + " ")
			} else {
				rendered = m.styles.ActiveTab.Render(tab)
			}
		} else {
			rendered = m.styles.Tab.Render(tab)
		}
		w := lipgloss.Width(rendered)
		m.tabWidths = append(m.tabWidths, w)
		tabs = append(tabs, rendered)
	}
	return lipgloss.JoinHorizontal(lipgloss.Top, tabs...)
}

func (m model) renderTabContent() string {
	switch m.activeTab {
	case tabOverview:
		return m.renderOverview()
	case tabAgents:
		return m.renderAgents()
	case tabTasks:
		return m.renderTasks()
	case tabMemory:
		return m.renderMemory()
	case tabRecall:
		return m.renderRecall()
	case tabSettings:
		return m.renderSettings()
	default:
		return "unknown tab"
	}
}

func (m model) renderDivider(label string) string {
	w := m.dashContentWidth()
	if w < 4 {
		w = 4
	}
	if label == "" {
		return m.styles.KeyLine.Render("\u251C" + strings.Repeat("\u2500", max(1, w-2)) + "\u2524")
	}
	label = " " + label + " "
	sides := w - lipgloss.Width(label)
	if sides < 4 {
		sides = 4
	}
	left := sides / 2
	right := sides - left
	return m.styles.KeyLine.Render("\u251C"+strings.Repeat("\u2500", max(1, left))) + label + m.styles.KeyLine.Render(strings.Repeat("\u2500", max(1, right))+"\u2524")
}

func (m model) renderBar(filled, total int, width int) string {
	if total <= 0 {
		total = 1
	}
	if width < 1 {
		width = 1
	}
	pct := float64(filled) / float64(total)
	if pct > 1.0 {
		pct = 1.0
	}
	filledW := int(float64(width) * pct)
	partialW := 0
	if filledW < width && pct > 0 {
		partialW = 1
	}
	emptyW := width - filledW - partialW
	if emptyW < 0 {
		emptyW = 0
	}
	if filledW < 0 {
		filledW = 0
	}
	bar := m.styles.BarFilled.Render(strings.Repeat("\u2588", filledW))
	if partialW > 0 {
		bar += m.styles.BarPartial.Render("\u2593")
	}
	bar += m.styles.BarEmpty.Render(strings.Repeat("\u2591", emptyW))
	return bar
}

func (m model) renderOverview() string {
	var lines []string

	lines = append(lines, m.renderDivider("Health"))
	if m.health != nil {
		dbIcon := "\u2713"
		dbColor := m.styles.Success
		if !m.health.DBExists {
			dbIcon = "\u2717"
			dbColor = m.styles.Error
		}
		lines = append(lines, fmt.Sprintf("  %s v%s  %s  %d sessions",
			m.styles.DotGreen, m.health.MemoriaVersion, dbColor.Render(dbIcon), m.health.SessionsIndexed))
		barW := m.dashContentWidth() - 12
		if barW > 6 {
			sessBarFilled := min(m.health.SessionsIndexed, 100)
			lines = append(lines, "  "+m.renderBar(sessBarFilled, 100, barW))
		}
		if len(m.health.Topics) > 0 {
			topicsStr := strings.Join(m.health.Topics, ", ")
			topicMax := m.dashContentWidth() - 12
			if topicMax < 20 {
				topicMax = 20
			}
			if len(topicsStr) > topicMax {
				topicsStr = topicsStr[:topicMax] + "\u2026"
			}
			lines = append(lines, fmt.Sprintf("  %s %s", m.styles.Dim.Render("topics:"), topicsStr))
		}
	} else {
		lines = append(lines, "  "+m.styles.DotRed+" unreachable")
	}
	lines = append(lines, "")

	activeAgents := 0
	idleAgents := 0
	errorAgents := 0
	for _, a := range m.agents {
		switch a.Status {
		case "active":
			activeAgents++
		case "idle", "pending":
			idleAgents++
		case "error", "failed", "stale":
			errorAgents++
		default:
			idleAgents++
		}
	}
	totalAgents := len(m.agents)
	if totalAgents == 0 {
		totalAgents = 1
	}
	lines = append(lines, m.renderDivider("Agents"))
	barW := m.dashContentWidth() - 6
	if barW > 6 {
		lines = append(lines, "  "+m.renderBar(activeAgents, totalAgents, barW/3)+
			m.renderBar(idleAgents, totalAgents, barW/3)+
			m.renderBar(errorAgents, totalAgents, barW/3))
	}
	agentLine := fmt.Sprintf("  %s %d active  %s %d idle", m.styles.DotGreen, activeAgents, m.styles.DotYellow, idleAgents)
	if errorAgents > 0 {
		agentLine += fmt.Sprintf("  %s %d error", m.styles.DotRed, errorAgents)
	}
	lines = append(lines, agentLine)
	lines = append(lines, "")

	doneTasks := 0
	progressTasks := 0
	failedTasks := 0
	for _, t := range m.tasks {
		switch t.Status {
		case "done", "completed":
			doneTasks++
		case "in_progress", "assigned":
			progressTasks++
		case "failed", "blocked":
			failedTasks++
		}
	}
	totalTasks := len(m.tasks)
	if totalTasks == 0 {
		totalTasks = 1
	}
	lines = append(lines, m.renderDivider("Tasks"))
	barW = m.dashContentWidth() - 6
	if barW > 6 {
		lines = append(lines, "  "+m.renderBar(doneTasks, totalTasks, barW/3)+
			m.renderBar(progressTasks, totalTasks, barW/3)+
			m.renderBar(failedTasks, totalTasks, barW/3))
	}
	taskLine := fmt.Sprintf("  %s %d done  %s %d running", m.styles.DotGreen, doneTasks, m.styles.DotYellow, progressTasks)
	if failedTasks > 0 {
		taskLine += fmt.Sprintf("  %s %d failed", m.styles.DotRed, failedTasks)
	}
	lines = append(lines, taskLine)
	lines = append(lines, "")

	if m.memory != nil {
		lines = append(lines, m.renderDivider("Memory"))
		lines = append(lines, fmt.Sprintf("  %s %s (%d entries)", m.styles.DotGreen, m.memory.Project, m.memory.Count))
		barW = m.dashContentWidth() - 6
		if barW > 6 {
			memPct := min(m.memory.Count, 50)
			lines = append(lines, "  "+m.renderBar(memPct, 50, barW))
		}
		lines = append(lines, "")
	}

	lines = append(lines, m.renderDivider("Rooms"))
	for _, room := range m.rooms {
		cnt := m.chitchat.MessageCount(room)
		badge := m.styles.Badge.Render(room)
		lines = append(lines, fmt.Sprintf("  %s %d msgs", badge, cnt))
	}

	lines = append(lines, "")
	lines = append(lines, m.styles.Dim.Render("[1-6] tab  [Enter] detail  [t] theme"))

	return strings.Join(lines, "\n")
}

func (m model) dashContentWidth() int {
	gap := 2
	w := m.width
	var chatWidth int
	switch {
	case w >= 160:
		chatWidth = w * 50 / 100
	case w >= 100:
		chatWidth = w * 45 / 100
	default:
		chatWidth = w * 55 / 100
	}
	minChatWidth := max(30, w/4)
	if chatWidth < minChatWidth {
		chatWidth = minChatWidth
	}
	maxChatWidth := w - gap - 20
	if chatWidth > maxChatWidth {
		chatWidth = maxChatWidth
	}
	dashWidth := w - chatWidth - gap
	if dashWidth < 20 {
		dashWidth = 20
	}
	return max(20, dashWidth-8)
}

func (m model) chatContentWidth() int {
	gap := 2
	w := m.width
	var chatWidth int
	switch {
	case w >= 160:
		chatWidth = w * 50 / 100
	case w >= 100:
		chatWidth = w * 45 / 100
	default:
		chatWidth = w * 55 / 100
	}
	minChatWidth := max(30, w/4)
	if chatWidth < minChatWidth {
		chatWidth = minChatWidth
	}
	maxChatWidth := w - gap - 20
	if chatWidth > maxChatWidth {
		chatWidth = maxChatWidth
	}
	return max(20, chatWidth-8)
}

func wrapString(s string, width int) []string {
	var lines []string
	for _, line := range strings.Split(s, "\n") {
		for len(line) > width {
			brk := width
			if idx := strings.LastIndex(line[:width], " "); idx > 0 {
				brk = idx
			}
			lines = append(lines, line[:brk])
			line = line[brk:]
			line = strings.TrimLeft(line, " ")
		}
		lines = append(lines, line)
	}
	return lines
}

func (m model) renderAgents() string {
	if len(m.agents) == 0 {
		return m.styles.Dim.Render("No active agents.")
	}
	maxW := m.dashContentWidth()

	if m.detailView && m.detailIdx >= 0 && m.detailIdx < len(m.agents) {
		return m.renderAgentDetail(m.agents[m.detailIdx], maxW)
	}

	var lines []string
	lines = append(lines, m.renderDivider("Agents"))
	for i, a := range m.agents {
		icon := "\u25CB"
		switch a.Status {
		case "active":
			icon = "\u25C9"
		case "idle", "pending":
			icon = "\u25CE"
		case "error", "failed", "stale":
			icon = "\u2717"
		}
		id := a.ID
		idMax := max(8, maxW/5)
		if len(id) > idMax {
			id = id[:idMax] + "\u2026"
		}
		taskVal := a.Task
		taskMax := max(10, maxW-len(a.Status)-22)
		if len(taskVal) > taskMax {
			taskVal = taskVal[:taskMax] + "\u2026"
		}
		projectBadge := m.styles.Badge.Render(a.Project)
		statusBadge := ""
		switch a.Status {
		case "active":
			statusBadge = m.styles.Success.Render("active")
		case "idle", "pending":
			statusBadge = m.styles.Dim.Render(a.Status)
		case "error", "failed", "stale":
			statusBadge = m.styles.Error.Render(a.Status)
		default:
			statusBadge = m.styles.Dim.Render(a.Status)
		}
		line := fmt.Sprintf(" %s %s %s %s %s", icon, m.styles.SettingsKey.Render(id), projectBadge, taskVal, statusBadge)
		line2 := ""
		if len(a.Files) > 0 {
			line2 = fmt.Sprintf("   %s %s %s", m.styles.Divider.Render("\u2502"), m.styles.Dim.Render(fmt.Sprintf("\u2588%d", len(a.Files))), m.styles.Dim.Render("files"))
			hbBar := time.Unix(int64(a.LastHeartbeat), 0)
			elapsed := time.Since(hbBar).Truncate(time.Second)
			line2 += fmt.Sprintf("  %s", m.styles.Dim.Render(elapsed.String()+" ago"))
		} else {
			hbBar := time.Unix(int64(a.LastHeartbeat), 0)
			elapsed := time.Since(hbBar).Truncate(time.Second)
			line2 = fmt.Sprintf("   %s %s", m.styles.Divider.Render("\u2502"), m.styles.Dim.Render(elapsed.String()+" ago"))
		}
		if m.focusMode == focusDashboard && m.activeTab == tabAgents && i == m.agentFocusIdx {
			line = m.styles.FocusStyle.Render(line)
			line2 = m.styles.FocusStyle.Render(line2)
		}
		lines = append(lines, line)
		lines = append(lines, line2)
	}
	lines = append(lines, m.styles.Dim.Render("[Enter] detail  [d] close"))
	return strings.Join(lines, "\n")
}

func (m model) renderAgentDetail(a AgentInfo, maxW int) string {
	var lines []string
	lines = append(lines, m.renderDivider("Agent Detail"))
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 ID:"), m.styles.StatValue.Render(a.ID)))
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Project:"), m.styles.StatValue.Render(a.Project)))
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Status:"), m.styles.StatValue.Render(a.Status)))
	if a.Task != "" {
		taskVal := a.Task
		if len(taskVal) > maxW-10 {
			taskVal = taskVal[:maxW-10] + "\u2026"
		}
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Task:"), taskVal))
	}
	started := time.Unix(int64(a.StartedAt), 0).Format("2006-01-02 15:04")
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Started:"), started))
	hbBar := time.Unix(int64(a.LastHeartbeat), 0)
	elapsed := time.Since(hbBar).Truncate(time.Second)
	barW := max(6, min(maxW-30, 20))
	barPct := 1.0
	if elapsed < 5*time.Minute {
		barPct = 1.0
	} else if elapsed < 10*time.Minute {
		barPct = 0.5
	} else {
		barPct = 0.15
	}
	filledW := int(float64(barW) * barPct)
	if filledW < 1 {
		filledW = 1
	}
	hbBar2 := m.styles.BarFilled.Render(strings.Repeat("\u2588", filledW)) + m.styles.BarEmpty.Render(strings.Repeat("\u2591", barW-filledW))
	lines = append(lines, fmt.Sprintf("  %s %s %s", m.styles.StatLabel.Render("\u2502 Heartbeat:"), hbBar2, m.styles.Dim.Render(elapsed.String()+" ago")))
	if len(a.Files) > 0 {
		lines = append(lines, fmt.Sprintf("  %s %d", m.styles.StatLabel.Render("\u2502 Files:"), len(a.Files)))
		fileMax := max(10, maxW-8)
		for _, f := range a.Files {
			fDisplay := f
			if len(fDisplay) > fileMax {
				fDisplay = fDisplay[:fileMax] + "\u2026"
			}
			lines = append(lines, fmt.Sprintf("  %s   %s", m.styles.Divider.Render("\u2502"), m.styles.Dim.Render(fDisplay)))
		}
	}
	if len(a.CommitLog) > 0 {
		lines = append(lines, fmt.Sprintf("  %s %d commits", m.styles.StatLabel.Render("\u2502 Commits:"), len(a.CommitLog)))
		commitMax := max(10, maxW-8)
		for i, c := range a.CommitLog {
			if i >= 5 {
				lines = append(lines, m.styles.Dim.Render(fmt.Sprintf("  \u2502   ... and %d more", len(a.CommitLog)-5)))
				break
			}
			cDisplay := c
			if len(cDisplay) > commitMax {
				cDisplay = cDisplay[:commitMax] + "\u2026"
			}
			lines = append(lines, fmt.Sprintf("  %s   %s", m.styles.Divider.Render("\u2502"), cDisplay))
		}
	}
	if len(a.ConflictsWarned) > 0 {
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.Error.Render("\u2502 Conflicts:"), strings.Join(a.ConflictsWarned, ", ")))
	}
	lines = append(lines, m.styles.KeyLine.Render("  \u2514"+strings.Repeat("\u2500", max(8, maxW-4))))
	lines = append(lines, m.styles.Dim.Render("[Enter/d] close detail"))
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
	maxW := m.dashContentWidth()

	if m.detailView && m.detailIdx >= 0 && m.detailIdx < len(m.tasks) {
		return m.renderTaskDetail(m.tasks[m.detailIdx], maxW)
	}

	var lines []string
	lines = append(lines, m.renderDivider("Tasks"))
	for i, t := range m.tasks {
		icon := "\u25CB"
		var statusBar string
		switch t.Status {
		case "done", "completed":
			icon = "\u2713"
			barW := max(4, min(maxW-len(t.ID)-20, 12))
			statusBar = m.styles.BarFilled.Render(strings.Repeat("\u2588", barW))
		case "in_progress", "assigned":
			icon = "\u27F3"
			barW := max(4, min(maxW-len(t.ID)-20, 12))
			half := barW / 2
			statusBar = m.styles.BarFilled.Render(strings.Repeat("\u2588", half)) +
				m.styles.BarEmpty.Render(strings.Repeat("\u2591", barW-half))
		case "failed", "blocked":
			icon = "\u2717"
			barW := max(4, min(maxW-len(t.ID)-20, 12))
			statusBar = m.styles.Error.Render(strings.Repeat("\u2593", barW))
		default:
			statusBar = ""
		}
		id := t.ID
		idMax := max(8, maxW/5)
		if len(id) > idMax {
			id = id[:idMax] + "\u2026"
		}
		titleMax := max(15, maxW-len(t.Status)-22)
		title := t.Title
		if len(title) > titleMax {
			title = title[:titleMax] + "\u2026"
		}
		assigned := t.AssignedTo
		if assigned == "" {
			assigned = "unassigned"
		}
		projectBadge := m.styles.Badge.Render(t.Project)
		line := fmt.Sprintf(" %s %s %s %s %s", icon, m.styles.SettingsKey.Render(id), projectBadge, title, m.styles.Dim.Render(assigned))
		if statusBar != "" {
			line += " " + statusBar
		}
		if m.focusMode == focusDashboard && m.activeTab == tabTasks && i == m.taskFocusIdx {
			line = m.styles.FocusStyle.Render(line)
		}
		lines = append(lines, line)
	}
	lines = append(lines, m.styles.Dim.Render("[Enter] detail  [d] close"))
	return strings.Join(lines, "\n")
}

func (m model) renderTaskDetail(t TaskInfo, maxW int) string {
	var lines []string
	lines = append(lines, m.renderDivider("Task Detail"))
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 ID:"), m.styles.StatValue.Render(t.ID)))
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Project:"), m.styles.StatValue.Render(t.Project)))
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Title:"), m.styles.StatValue.Render(t.Title)))
	statusBar := ""
	barW := max(6, min(maxW-30, 20))
	switch t.Status {
	case "done", "completed":
		statusBar = " " + m.styles.BarFilled.Render(strings.Repeat("\u2588", barW))
	case "in_progress", "assigned":
		half := barW / 2
		statusBar = " " + m.styles.BarFilled.Render(strings.Repeat("\u2588", half)) + m.styles.BarEmpty.Render(strings.Repeat("\u2591", barW-half))
	case "failed", "blocked":
		statusBar = " " + m.styles.Error.Render(strings.Repeat("\u2593", barW))
	}
	lines = append(lines, fmt.Sprintf("  %s %s%s", m.styles.StatLabel.Render("\u2502 Status:"), m.styles.StatValue.Render(t.Status), statusBar))
	if t.AssignedTo != "" {
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Assigned:"), m.styles.StatValue.Render(t.AssignedTo)))
	} else {
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Assigned:"), m.styles.Dim.Render("unassigned")))
	}
	if t.Result != "" {
		resultVal := t.Result
		if len(resultVal) > maxW-6 {
			resultVal = resultVal[:maxW-6] + "\u2026"
		}
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Result:"), resultVal))
	}
	if t.Error != "" {
		errVal := t.Error
		if len(errVal) > maxW-6 {
			errVal = errVal[:maxW-6] + "\u2026"
		}
		lines = append(lines, fmt.Sprintf("  %s %s", m.styles.Error.Render("\u2502 Error:"), m.styles.Error.Render(errVal)))
	}
	created := time.Unix(int64(t.CreatedAt), 0).Format("2006-01-02 15:04")
	lines = append(lines, fmt.Sprintf("  %s %s", m.styles.StatLabel.Render("\u2502 Created:"), created))
	lines = append(lines, m.styles.KeyLine.Render("  \u2514"+strings.Repeat("\u2500", max(8, maxW-4))))
	lines = append(lines, m.styles.Dim.Render("[Enter/d] close detail"))
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
	maxW := m.dashContentWidth()
	var lines []string
	lines = append(lines, m.renderDivider("Memory"))
	lines = append(lines, m.styles.Info.Render(fmt.Sprintf("  %s (%d entries)", m.memory.Project, m.memory.Count)))
	if len(m.memory.Entries) == 0 {
		lines = append(lines, m.styles.Dim.Render("  No memory entries."))
	} else {
		entryLimit := len(m.memory.Entries)
		if entryLimit > 40 {
			entryLimit = 40
		}
		for i, e := range m.memory.Entries {
			if i >= entryLimit {
				lines = append(lines, m.styles.Dim.Render(fmt.Sprintf("  ... and %d more", len(m.memory.Entries)-entryLimit)))
				break
			}
			textMax := max(30, maxW-6)
			text := e
			if len(text) > textMax {
				text = text[:textMax] + "\u2026"
			}
			lines = append(lines, m.styles.Divider.Render("\u2502")+" "+text)
		}
	}
	lines = append(lines, "")
	lines = append(lines, m.styles.Dim.Render("[Enter] load another project"))
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
	maxW := m.dashContentWidth()
	var lines []string
	lines = append(lines, m.renderDivider(fmt.Sprintf("Recall: %s (%d)", m.recallQuery, len(m.recall))))
	for _, r := range m.recall {
		id := r.ID
		idMax := max(8, maxW/6)
		if len(id) > idMax {
			id = id[:idMax]
		}
		titleMax := max(20, maxW-idMax-10)
		title := r.Title
		if len(title) > titleMax {
			title = title[:titleMax] + "\u2026"
		}
		sourceBadge := ""
		if r.Source != "" {
			sourceBadge = " " + m.styles.Badge.Render(r.Source)
		}
		roomBadge := ""
		if r.Room != "" {
			roomBadge = " " + m.styles.Badge.Render(r.Room)
		}
		fromLabel := ""
		if r.From != "" {
			fromLabel = " " + m.styles.Dim.Render("from:"+r.From)
		}
		lines = append(lines, fmt.Sprintf("  %s %s%s%s%s", m.styles.SettingsKey.Render("["+id+"]"), title, sourceBadge, roomBadge, fromLabel))
		summaryMax := max(30, maxW-8)
		summary := r.Summary
		if len(summary) > summaryMax {
			summary = summary[:summaryMax] + "\u2026"
		}
		lines = append(lines, fmt.Sprintf("       %s", m.styles.Dim.Render(summary)))
	}
	lines = append(lines, "")
	lines = append(lines, m.styles.Dim.Render("[r] search again"))
	return strings.Join(lines, "\n")
}

func (m model) renderChatMessages() string {
	if len(m.chatMessages) == 0 {
		return m.styles.Dim.Render("No messages in this room yet.")
	}
	chatWidth := m.chatContentWidth()
	var lines []string
	dividerWidth := max(6, chatWidth)
	for _, msg := range m.chatMessages {
		if m.hideAgentOs && (strings.Contains(msg.From, "agent-os") || strings.Contains(msg.From, "mini-participant")) {
			continue
		}
		if m.hideSystem && msg.Type == "system" {
			continue
		}
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

		tsStr := m.styles.Timestamp.Render(ts)

		var icon string
		var senderStr string
		if from == "you" || from == "user" {
			icon = "\u25B6"
			senderStr = m.styles.UserMsg.Render(from)
		} else if msg.Type == "system" {
			icon = "\u25C9"
			senderStr = m.styles.SystemMsg.Render(from)
		} else if strings.Contains(from, "agent-os") || strings.Contains(from, "participant") {
			icon = "\u25C8"
			senderStr = m.styles.Dim.Render(from)
		} else {
			icon = "\u25C8"
			senderStr = m.styles.AgentMsg.Render(from)
		}

		prefix := fmt.Sprintf(" %s %s %s ", tsStr, icon, senderStr)
		prefixWidth := lipgloss.Width(prefix)
		wrapWidth := chatWidth - prefixWidth - 1
		if wrapWidth < 15 {
			wrapWidth = 15
		}

		parts := wrapString(msg.Text, wrapWidth)
		for i, p := range parts {
			if i == 0 {
				lines = append(lines, prefix+p)
			} else {
				lines = append(lines, strings.Repeat(" ", prefixWidth)+p)
			}
		}
		lines = append(lines, m.styles.Divider.Render(strings.Repeat("\u2500", dividerWidth)))
	}
	if len(lines) > 0 {
		lines = lines[:len(lines)-1]
	}
	return strings.Join(lines, "\n")
}

func (m model) renderStatusBar() string {
	healthDot := m.styles.DotRed
	if m.health != nil {
		healthDot = m.styles.DotGreen
	}

	tabNames := []string{"Overview", "Agents", "Tasks", "Memory", "Recall", "Settings"}
	tabLabel := ""
	if m.activeTab >= 0 && m.activeTab < len(tabNames) {
		tabLabel = " " + tabNames[m.activeTab]
	}

	focusLabel := ""
	switch m.focusMode {
	case focusChat:
		focusLabel = " chat"
	case focusDashboard:
		focusLabel = " dash"
	case focusInput:
		focusLabel = " type"
	}

	filterLabel := ""
	if m.hideAgentOs || m.hideSystem {
		filterLabel = " filter:"
		if m.hideAgentOs {
			filterLabel += " -agent-os"
		}
		if m.hideSystem {
			filterLabel += " -system"
		}
	}

	left := fmt.Sprintf(" %s mem%s %s chat%s%s %s",
		healthDot, tabLabel, m.styles.DotGreen, focusLabel, filterLabel, m.rooms[m.activeRoom])
	right := " [?] help [q] quit [f] filter "
	spacing := m.width - lipgloss.Width(left) - lipgloss.Width(right)
	if spacing < 1 {
		spacing = 1
	}
	bar := left + strings.Repeat(" ", spacing) + right
	return m.styles.StatusBar.Render(bar)
}

func (m model) renderHelpFooter() string {
	var hints []string
	switch m.focusMode {
	case focusChat:
		hints = []string{"↑↓ scroll", "←→ rooms", "Tab dash", "i type", "f filter"}
	case focusDashboard:
		hints = []string{"1-6 tab", "↑↓ nav", "Enter act", "Tab chat", "f filter"}
	case focusInput:
		hints = []string{"Enter send", "Esc back", "Tab dash"}
	}
	return m.styles.Dim.Render(" " + strings.Join(hints, " │ ") + " ")
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
		fmt.Println("  h/l             Switch dashboard tabs")
		fmt.Println("  ↑↓ / j/k        Navigate tabs / list items")
		fmt.Println("  ← →             Switch chat rooms")
		fmt.Println("  1-6             Jump to dashboard tab")
		fmt.Println("  i / Enter       Enter chat input mode")
		fmt.Println("  Esc             Exit input, return to navigation")
		fmt.Println("  Tab             Cycle through interactive zones")
		fmt.Println("  Enter           Activate / send / detail toggle")
		fmt.Println("  d               Close detail view")
		fmt.Println("  r               Focus recall search")
		fmt.Println("  t               Next theme")
		fmt.Println("  c               Force consolidate (Settings)")
		fmt.Println("  p               Clear proposals (Settings)")
		fmt.Println("  ?               Toggle help in status bar")
		fmt.Println("  q / ctrl+c      Quit")
		fmt.Println("  Mouse click     Select tabs, items, rooms, chat input")
		fmt.Println("  Scroll wheel    Scroll chat viewport")
		fmt.Println("  f               Toggle agent-os filter")
		fmt.Println("  F               Toggle system message filter")
		return
	}
	p := tea.NewProgram(initialModel(), tea.WithAltScreen(), tea.WithMouseCellMotion())
	if _, err := p.Run(); err != nil {
		panic(err)
	}
}