package main

import "github.com/charmbracelet/lipgloss"

var (
	docStyle = lipgloss.NewStyle().Padding(1, 2)

	appStyle = lipgloss.NewStyle().Margin(1, 0)

	leftStyle = lipgloss.NewStyle().
			Width(50).
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("63")).
			Padding(0, 1)

	rightStyle = lipgloss.NewStyle().
			Width(50).
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("63")).
			Padding(0, 1)

	chatViewStyle = lipgloss.NewStyle().
			Padding(0, 1).
			MaxWidth(60)

	inputStyle = lipgloss.NewStyle().
			Border(lipgloss.NormalBorder()).
			BorderForeground(lipgloss.Color("240")).
			Padding(0, 1)

	statusStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("236")).
			Background(lipgloss.Color("254")).
			Padding(0, 1).
			Align(lipgloss.Left)

	tabStyle = lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(lipgloss.Color("248"))

	activeTabStyle = lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(lipgloss.Color("229")).
			Background(lipgloss.Color("63"))

	infoStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("248")).
			Padding(0, 1)

	agentStatusStyle = lipgloss.NewStyle().
				Width(10).
				Align(lipgloss.Center).
				Padding(0, 1).
				Border(lipgloss.RoundedBorder())

	taskStatusStyle = lipgloss.NewStyle().
			Width(10).
			Align(lipgloss.Center).
			Padding(0, 1).
			Border(lipgloss.RoundedBorder())

	roomTabStyle = lipgloss.NewStyle().
			Padding(0, 2).
			Foreground(lipgloss.Color("248"))

	activeRoomTabStyle = lipgloss.NewStyle().
				Padding(0, 2).
				Foreground(lipgloss.Color("229")).
				Background(lipgloss.Color("63"))

	errorStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("196"))

	successStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("46"))

	spinnerStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("63"))

	helpStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("240")).
			Padding(0, 1)

	promptStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("46")).
			Bold(true)

	dimStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("240"))
)
