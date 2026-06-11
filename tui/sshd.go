package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/ssh"
	"github.com/charmbracelet/wish"
	"github.com/charmbracelet/wish/activeterm"
	"github.com/charmbracelet/wish/logging"
)

const (
	sshPort     = 23234
	sshHost     = "0.0.0.0"
	hostKeyPath = "/var/tmp/memoria/tui_host_key"
)

func startSSHServer() error {
	os.MkdirAll("/var/tmp/memoria", 0755)

	srv, err := wish.NewServer(
		wish.WithAddress(fmt.Sprintf("%s:%d", sshHost, sshPort)),
		wish.WithHostKeyPath(hostKeyPath),
		wish.WithMiddleware(
			activeterm.Middleware(),
			logging.Middleware(),
			wish.Middleware(sshTUIHandler),
		),
	)
	if err != nil {
		return fmt.Errorf("create ssh server: %w", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		log.Printf("memoria TUI SSH server on %s:%d", sshHost, sshPort)
		if err := srv.ListenAndServe(); err != nil {
			log.Printf("ssh server error: %v", err)
		}
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	select {
	case <-ctx.Done():
	case <-sigCh:
	}

	log.Println("shutting down SSH server...")
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()
	srv.Shutdown(shutdownCtx)
	wg.Wait()
	return nil
}

func sshTUIHandler(next ssh.Handler) ssh.Handler {
	return func(s ssh.Session) {
		pty, winCh, _ := s.Pty()

		model := initialModel()

		p := tea.NewProgram(
			model,
			tea.WithInput(s),
			tea.WithOutput(s),
			tea.WithAltScreen(),
			tea.WithMouseCellMotion(),
		)

		if pty.Window.Height > 0 && pty.Window.Width > 0 {
			p.Send(tea.WindowSizeMsg{
				Width:  pty.Window.Width,
				Height: pty.Window.Height,
			})
		}

		go func() {
			for win := range winCh {
				p.Send(tea.WindowSizeMsg{Width: win.Width, Height: win.Height})
			}
		}()

		if _, err := p.Run(); err != nil {
			log.Printf("TUI error: %v", err)
		}

		if next != nil {
			next(s)
		}
	}
}
